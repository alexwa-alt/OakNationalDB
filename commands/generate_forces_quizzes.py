#!/usr/bin/env python3
"""Generate validated cached KS3 science quizzes with Gemini."""

from __future__ import annotations

import argparse
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
    parser.add_argument("--delay-seconds", type=float, default=60)
    parser.add_argument("--overwrite", action="store_true")
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


def prompt_for(unit_key: str, points: list[str], variation: int) -> str:
    numbered_points = "\n".join(
        f"{index}. {point}" for index, point in enumerate(points)
    )
    return f"""Create a distinct KS3 science multiple-choice quiz about {unit_key}.

Use only the supplied learning points. Produce 15 to 20 questions. Each question
must have exactly four plausible, distinct options, one correct answer, and a
learningPointIndex matching the zero-based index of a supplied learning point it
assesses. Do not use "all of the above", "none of the above", or questions that
depend on information outside these points. Vary concepts and wording from other
possible quizzes; this is variation {variation}.

Learning points:
{numbered_points}
"""


def response_text(response: dict[str, Any]) -> str:
    try:
        parts = response["candidates"][0]["content"]["parts"]
        return "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Gemini response did not contain candidate text") from error


def validate_quiz(payload: Any, point_count: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise ValueError("Quiz payload must contain a questions array")
    questions = payload["questions"]
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
    api_key: str, model: str, unit_key: str, points: list[str], variation: int
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
                            "parts": [{"text": prompt_for(unit_key, points, variation)}],
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
            except json.JSONDecodeError as error:
                raise ValueError("Gemini response was not valid JSON") from error
            return validate_quiz(payload, len(points))
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


def valid_quiz_file(path: Path, point_count: int) -> bool:
    try:
        validate_quiz(json.loads(path.read_text(encoding="utf-8")), point_count)
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


def main() -> int:
    arguments = parse_arguments()
    if not 1 <= arguments.count <= 5:
        raise ValueError("--count must be between 1 and 5")
    if arguments.delay_seconds < 0:
        raise ValueError("--delay-seconds cannot be negative")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY must be set")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    units = load_units()
    paths = unit_paths(units)
    selected_keys = selected_unit_keys(arguments, units)
    manifest = load_manifest()
    manifest_units: dict[str, Any] = manifest["units"]
    last_request_at: float | None = None

    for unit_key in selected_keys:
        points = units[unit_key]
        directory = QUIZZES_DIRECTORY / paths[unit_key]
        files: list[str] = []
        for variation in range(1, arguments.count + 1):
            output_path = directory / f"quiz-{variation}.json"
            if output_path.exists() and not arguments.overwrite and valid_quiz_file(
                output_path, len(points)
            ):
                print(f"Keeping existing {output_path}")
            else:
                if last_request_at is not None:
                    wait = arguments.delay_seconds - (time.monotonic() - last_request_at)
                    if wait > 0:
                        time.sleep(wait)
                print(f"Generating {unit_key} quiz {variation}/{arguments.count} with {model}")
                last_request_at = time.monotonic()
                questions = generate_quiz(api_key, model, unit_key, points, variation)
                write_json(
                    output_path,
                    {
                        "unitKey": unit_key,
                        "generatedAt": datetime.now(timezone.utc).isoformat(),
                        "questions": questions,
                    },
                )
            files.append(output_path.name)
        write_json(directory / "index.json", {"quizzes": files})
        manifest_units[unit_key] = {"path": paths[unit_key]}
        write_json(QUIZZES_DIRECTORY / "index.json", manifest)
        print(f"Published cache manifest entry for {unit_key}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, requests.RequestException) as error:
        print(f"Quiz generation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
