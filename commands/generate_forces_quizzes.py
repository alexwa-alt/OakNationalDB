#!/usr/bin/env python3
"""Generate validated cached KS3 science quizzes with Gemini."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DEFAULT_UNIT_KEY = "1ForcesPhysics"
QUIZZES_DIRECTORY = Path("site/quizzes")
UNITS_FILE = Path("site/ks3_units.json")
API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
REQUEST_TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 4

QUIZ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 15,
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "string"},
                    },
                    "correctAnswerIndex": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                    },
                    "learningPointIndex": {
                        "type": "integer",
                        "minimum": 0,
                    },
                },
                "required": [
                    "question",
                    "options",
                    "correctAnswerIndex",
                    "learningPointIndex",
                ],
            },
        }
    },
    "required": ["questions"],
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", default=DEFAULT_UNIT_KEY)
    parser.add_argument("--all", action="store_true", dest="all_units")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--delay-seconds", type=float, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--required-requests", action="store_true")
    parser.add_argument("--challenging", action="store_true")
    return parser.parse_args()


def load_units() -> dict[str, list[str]]:
    units = json.loads(UNITS_FILE.read_text(encoding="utf-8"))
    if not isinstance(units, dict):
        raise ValueError(f"{UNITS_FILE} must contain an object")
    valid_units: dict[str, list[str]] = {}
    for unit_key, points in units.items():
        if not isinstance(unit_key, str) or not isinstance(points, list) or not all(
            isinstance(point, str) and point.strip() for point in points
        ):
            raise ValueError(f"{UNITS_FILE} contains invalid learning points for {unit_key}")
        valid_units[unit_key] = points
    return valid_units


def unit_paths(units: dict[str, list[str]]) -> dict[str, str]:
    paths: dict[str, str] = {}
    used_paths: set[str] = set()
    for unit_key in units:
        if unit_key == DEFAULT_UNIT_KEY:
            path = "forces"
        else:
            readable = re.sub(r"^\\d+", "", unit_key)
            readable = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", readable)
            path = re.sub(r"[^a-z0-9]+", "-", readable.lower()).strip("-")
        if path in used_paths:
            path = f"{path}-{hashlib.sha256(unit_key.encode()).hexdigest()[:8]}"
        paths[unit_key] = path
        used_paths.add(path)
    return paths


def prompt_for(
    unit_key: str, points: list[str], variation: int, challenging: bool
) -> str:
    numbered_points = "\n".join(
        f"{index}. {point}" for index, point in enumerate(points)
    )
    difficulty = (
        """Make every question challenging: require careful application, comparison,
multi-step reasoning, identifying misconceptions, or interpreting unfamiliar
contexts. Do not make questions difficult through obscure vocabulary."""
        if challenging
        else "Use age-appropriate KS3 difficulty."
    )
    return f"""Create a distinct KS3 science multiple-choice quiz about {unit_key}.

Use only the supplied learning points. Produce exactly 20 questions. Each question
must have exactly four plausible, distinct options, one correct answer, and a
learningPointIndex matching the zero-based index of a supplied learning point it
assesses. Do not use "all of the above", "none of the above", or questions that
depend on information outside these points. Vary concepts and wording from other
possible quizzes; this is variation {variation}.

{difficulty}

Learning points:
{numbered_points}
"""


def response_text(response: dict[str, Any]) -> str:
    try:
        parts = response["candidates"][0]["content"]["parts"]
        return "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Gemini response did not contain candidate text") from error


def validate_quiz(
    payload: Any, point_count: int, exact_question_count: int | None = None
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("Quiz payload must contain a questions array")
    questions = payload["questions"]
    if exact_question_count is not None and len(questions) != exact_question_count:
        raise ValueError(f"Quiz must contain exactly {exact_question_count} questions")
    if not 15 <= len(questions) <= 20:
        raise ValueError("Quiz must contain 15 to 20 questions")

    validated: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {index} is not an object")
        question = item.get("question")
        options = item.get("options")
        answer_index = item.get("correctAnswerIndex")
        learning_point_index = item.get("learningPointIndex")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Question {index} has no text")
        if (
            not isinstance(options, list)
            or len(options) != 4
            or not all(isinstance(option, str) and option.strip() for option in options)
            or len({option.strip().casefold() for option in options}) != 4
        ):
            raise ValueError(f"Question {index} must have four distinct options")
        if not isinstance(answer_index, int) or not 0 <= answer_index < 4:
            raise ValueError(f"Question {index} has an invalid correctAnswerIndex")
        if (
            not isinstance(learning_point_index, int)
            or not 0 <= learning_point_index < point_count
        ):
            raise ValueError(f"Question {index} has an invalid learningPointIndex")
        validated.append(
            {
                "question": question.strip(),
                "options": [option.strip() for option in options],
                "correctAnswerIndex": answer_index,
                "learningPointIndex": learning_point_index,
            }
        )
    return validated


def generate_quiz(
    api_key: str,
    model: str,
    unit_key: str,
    points: list[str],
    variation: int,
    challenging: bool,
) -> list[dict[str, Any]]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                API_URL_TEMPLATE.format(model=model),
                params={"key": api_key},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": prompt_for(
                                        unit_key, points, variation, challenging
                                    )
                                }
                            ],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.9,
                        "responseMimeType": "application/json",
                        "responseJsonSchema": QUIZ_SCHEMA,
                    },
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Gemini request failed after {MAX_ATTEMPTS} connection attempts"
                ) from error
            delay = 60 * (2 ** (attempt - 1))
            print(
                f"Gemini request failed: {error}; retrying in {delay}s "
                f"(attempt {attempt}/{MAX_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        if response.ok:
            try:
                payload = json.loads(response_text(response.json()))
                return validate_quiz(
                    payload, len(points), 20 if challenging else None
                )
            except (json.JSONDecodeError, ValueError) as error:
                if attempt == MAX_ATTEMPTS:
                    raise ValueError(
                        f"Gemini returned an invalid quiz after {MAX_ATTEMPTS} attempts"
                    ) from error
                delay = 5 * attempt
                print(
                    f"Gemini returned an invalid quiz: {error}; retrying in {delay}s "
                    f"(attempt {attempt}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
        if response.status_code != 429 and not 500 <= response.status_code < 600:
            raise RuntimeError(
                f"Gemini request failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        if attempt == MAX_ATTEMPTS:
            break
        delay = 60 * (2 ** (attempt - 1))
        print(
            f"Gemini returned HTTP {response.status_code}; retrying in {delay}s "
            f"(attempt {attempt}/{MAX_ATTEMPTS})",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise RuntimeError(f"Gemini request failed after {MAX_ATTEMPTS} retryable attempts")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


def valid_quiz_file(
    path: Path, point_count: int, exact_question_count: int | None = None
) -> bool:
    try:
        validate_quiz(
            json.loads(path.read_text(encoding="utf-8")),
            point_count,
            exact_question_count,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return True


def selected_unit_keys(arguments: argparse.Namespace, units: dict[str, list[str]]) -> list[str]:
    if arguments.all_units:
        keys = list(units)
        if arguments.start < 0:
            raise ValueError("--start cannot be negative")
        keys = keys[arguments.start :]
        if arguments.limit is not None:
            if arguments.limit < 1:
                raise ValueError("--limit must be positive")
            keys = keys[: arguments.limit]
        return keys
    if arguments.unit not in units:
        raise ValueError(f"Unknown unit: {arguments.unit}")
    return [arguments.unit]


def load_manifest() -> dict[str, Any]:
    manifest_path = QUIZZES_DIRECTORY / "index.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"units": {}}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("units"), dict):
        raise ValueError(f"{manifest_path} is invalid")
    return manifest


def required_request_count(
    selected_keys: list[str],
    units: dict[str, list[str]],
    paths: dict[str, str],
    count: int,
    overwrite: bool,
    challenging: bool,
) -> int:
    required = 0
    for unit_key in selected_keys:
        points = units[unit_key]
        directory = QUIZZES_DIRECTORY / paths[unit_key]
        filenames = ["challenging.json"] if challenging else [
            f"quiz-{variation}.json" for variation in range(1, count + 1)
        ]
        for filename in filenames:
            output_path = directory / filename
            if overwrite or not valid_quiz_file(
                output_path, len(points), 20 if challenging else None
            ):
                required += 1
    return required


def main() -> int:
    arguments = parse_arguments()
    if not 1 <= arguments.count <= 5:
        raise ValueError("--count must be between 1 and 5")
    if arguments.delay_seconds < 0:
        raise ValueError("--delay-seconds cannot be negative")
    if not 1 <= arguments.workers <= 100:
        raise ValueError("--workers must be between 1 and 100")

    units = load_units()
    paths = unit_paths(units)
    selected_keys = selected_unit_keys(arguments, units)
    if arguments.required_requests:
        print(
            required_request_count(
                selected_keys,
                units,
                paths,
                arguments.count,
                arguments.overwrite,
                arguments.challenging,
            )
        )
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY must be set")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    manifest = load_manifest()
    manifest_units: dict[str, Any] = manifest["units"]
    requests_to_generate: list[tuple[str, list[str], int, Path]] = []
    for unit_key in selected_keys:
        points = units[unit_key]
        directory = QUIZZES_DIRECTORY / paths[unit_key]
        filenames = ["challenging.json"] if arguments.challenging else [
            f"quiz-{variation}.json" for variation in range(1, arguments.count + 1)
        ]
        for variation, filename in enumerate(filenames, start=1):
            output_path = directory / filename
            if output_path.exists() and not arguments.overwrite and valid_quiz_file(
                output_path, len(points), 20 if arguments.challenging else None
            ):
                print(f"Keeping existing {output_path}")
            else:
                requests_to_generate.append((unit_key, points, variation, output_path))

    def generate_and_write(
        unit_key: str, points: list[str], variation: int, output_path: Path
    ) -> None:
        print(f"Generating {unit_key} quiz {variation}/{arguments.count} with {model}")
        questions = generate_quiz(
            api_key, model, unit_key, points, variation, arguments.challenging
        )
        write_json(
            output_path,
            {
                "unitKey": unit_key,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "questions": questions,
            },
        )

    if arguments.workers == 1:
        for index, request in enumerate(requests_to_generate):
            if index:
                time.sleep(arguments.delay_seconds)
            generate_and_write(*request)
    else:
        with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
            futures = [
                executor.submit(generate_and_write, *request)
                for request in requests_to_generate
            ]
            for future in as_completed(futures):
                future.result()

    for unit_key in selected_keys:
        directory = QUIZZES_DIRECTORY / paths[unit_key]
        entry = manifest_units.setdefault(unit_key, {"path": paths[unit_key]})
        if arguments.challenging:
            entry["challenging"] = "challenging.json"
        else:
            files = [
                f"quiz-{variation}.json" for variation in range(1, arguments.count + 1)
            ]
            write_json(directory / "index.json", {"quizzes": files})
        write_json(QUIZZES_DIRECTORY / "index.json", manifest)
        print(f"Published cache manifest entry for {unit_key}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, requests.RequestException) as error:
        print(f"Quiz generation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
