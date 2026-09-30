from dataclasses import dataclass
import math


MODEL_VERSION = "ice-fsrs-1"
DESIRED_RETENTION = 0.9


@dataclass(frozen=True)
class Schedule:
    difficulty: float
    stability: float
    retrievability: float
    interval_days: float
    lapses: int


def retrievability(stability: float, elapsed_days: float) -> float:
    if stability <= 0:
        return 0.0
    return max(0.0, min(1.0, math.pow(1.0 + elapsed_days / (9.0 * stability), -1.0)))


def schedule_review(
    rating: str,
    *,
    difficulty: float = 5.0,
    stability: float = 0.0,
    elapsed_days: float = 0.0,
    lapses: int = 0,
) -> Schedule:
    """Independent deterministic scheduler based on published FSRS state concepts.

    Review logs remain immutable, so a later scheduler can replay them under a new
    model version without changing history.
    """
    grade = {"again": 1, "hard": 2, "good": 3, "easy": 4}[rating]
    if stability <= 0:
        next_stability = (0.4, 0.8, 2.4, 5.8)[grade - 1]
        next_difficulty = 5.0 - 0.6 * (grade - 3)
    else:
        recall = retrievability(stability, max(0.0, elapsed_days))
        next_difficulty = difficulty + (3 - grade) * 0.45
        if grade == 1:
            next_stability = max(0.25, stability * 0.35)
            lapses += 1
        else:
            grade_factor = {2: 0.75, 3: 1.0, 4: 1.35}[grade]
            growth = 1.0 + grade_factor * (11.0 - next_difficulty) * max(0.05, 1.0 - recall) * 0.35
            next_stability = max(stability + 0.05, stability * growth)
    next_difficulty = min(10.0, max(1.0, next_difficulty))
    interval = max(1.0, 9.0 * next_stability * (1.0 / DESIRED_RETENTION - 1.0))
    if grade == 1:
        interval = 1.0
    return Schedule(
        difficulty=round(next_difficulty, 6),
        stability=round(next_stability, 6),
        retrievability=DESIRED_RETENTION,
        interval_days=round(interval, 6),
        lapses=lapses,
    )
