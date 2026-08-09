#!/usr/bin/env python3
"""Generate validated cached quiz files for the KS3 Forces unit."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


UNIT_KEY = "1ForcesPhysics"
QUIZ_DIRECTORY = Path("site/quizzes/forces")
UNITS_FILE = Path("site/ks3_units.json")
API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
REQUEST_TIMEOUT_SECONDS = 90

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
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--delay-seconds", type=float, default=60)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_learning_points() -> list[str]:
    units = json.loads(UNITS_FILE.read_text(encoding="utf-8"))
    points = units.get(UNIT_KEY)
    if not isinstance(points, list) or not all(
        isinstance(point, str) and point.strip() for point in points
    ):
        raise ValueError(f"{UNITS_FILE} does not contain valid points for {UNIT_KEY}")
    return points


def prompt_for(points: list[str], variation: int) -> str:
    numbered_points = "\n".join(
        f"{index}. {point}" for index, point in enumerate(points)
    )
    return f"""Create a distinct KS3 science multiple-choice quiz about Forces.

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
    api_key: str, model: str, points: list[str], variation: int
) -> list[dict[str, Any]]:
    response = requests.post(
        API_URL_TEMPLATE.format(model=model),
        params={"key": api_key},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt_for(points, variation)}]}],
            "generationConfig": {
                "temperature": 0.9,
                "responseMimeType": "application/json",
                "responseJsonSchema": QUIZ_SCHEMA,
            },
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(
            f"Gemini request failed with HTTP {response.status_code}: {response.text[:500]}"
        )
    try:
        payload = json.loads(response_text(response.json()))
    except json.JSONDecodeError as error:
        raise ValueError("Gemini response was not valid JSON") from error
    return validate_quiz(payload, len(points))


def write_json(path: Path, value: Any) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def main() -> int:
    arguments = parse_arguments()
    if not 1 <= arguments.count <= 5:
        raise ValueError("--count must be between 1 and 5")
    if arguments.delay_seconds < 0:
        raise ValueError("--delay-seconds cannot be negative")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY must be set")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    points = load_learning_points()
    QUIZ_DIRECTORY.mkdir(parents=True, exist_ok=True)

    generated_files: list[str] = []
    for variation in range(1, arguments.count + 1):
        output_path = QUIZ_DIRECTORY / f"quiz-{variation}.json"
        if output_path.exists() and not arguments.overwrite:
            print(f"Keeping existing {output_path}")
            generated_files.append(output_path.name)
            continue
        print(f"Generating quiz {variation}/{arguments.count} with {model}")
        questions = generate_quiz(api_key, model, points, variation)
        write_json(
            output_path,
            {
                "unitKey": UNIT_KEY,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "questions": questions,
            },
        )
        generated_files.append(output_path.name)
        if variation < arguments.count:
            time.sleep(arguments.delay_seconds)

    write_json(QUIZ_DIRECTORY / "index.json", {"quizzes": generated_files})
    print(f"Wrote {len(generated_files)} quiz file(s) to {QUIZ_DIRECTORY}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, requests.RequestException) as error:
        print(f"Quiz generation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
