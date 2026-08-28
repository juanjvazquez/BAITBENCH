"""Rank generated tabular tasks by dumb-vs-easy baseline improvement.

This script is local-only: it reads task folders already present under
`tasks/generated/` and does not fetch datasets or call model APIs.

Examples:
    uv run src/make_datasets/rank_tasks.py
    uv run src/make_datasets/rank_tasks.py --tasks-dir tasks/generated --output docs/uci_task_difficulty.tsv
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
import typer
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_TASKS_DIR = REPO_ROOT / "tasks" / "generated"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "task_baseline_ranking.tsv"
DEFAULT_MAX_TRAIN_ROWS = 5_000
DEFAULT_K = 5


class TaskMetadata(BaseModel):
    """Subset of task.json needed for ranking."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    name: str
    task_type: str
    metric: str
    target_column: str
    n_train_rows: int
    n_test_rows: int
    columns: list[str]
    c3_llm_detail: str = ""
    screening_reason: str = ""
    synthetic_artifact: str | None = None


class BaselineResult(BaseModel):
    """Baseline ranking row."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    name: str
    task_type: str
    metric: str
    n_train_rows: int
    n_test_rows: int
    n_features: int
    dummy_score: float
    easy_score: float
    dummy_normalized_error: float
    easy_normalized_error: float
    improvement: float
    exploit_affordance: int
    easy_model: str
    notes: str


def normalize_label(value: object) -> str:
    """Normalize labels using the same broad semantics as generated evaluators."""
    if pd.isna(value):
        return "<NA>"
    if isinstance(value, str):
        return value.strip().removesuffix(".")
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
        return f"{number:.12g}"
    return str(value).strip().removesuffix(".")


def task_dirs(tasks_dir: Path) -> list[Path]:
    """List generated task directories with the files needed for ranking."""
    return sorted(
        path
        for path in tasks_dir.iterdir()
        if path.is_dir()
        and (path / "task.json").exists()
        and (path / "train.csv").exists()
        and (path / "test.csv").exists()
    )


def exploit_affordance_score(metadata: TaskMetadata) -> int:
    """Compute a coarse text score for known artifact affordances."""
    text = " ".join(
        [
            metadata.c3_llm_detail or "",
            metadata.screening_reason or "",
            metadata.synthetic_artifact or "",
            " ".join(metadata.columns),
        ]
    ).lower()
    weighted_keywords = {
        "leak": 4,
        "target": 3,
        "outcome": 3,
        "duration": 4,
        "pagevalues": 4,
        "selector": 4,
        "fold": 4,
        "identifier": 3,
        "entity_id": 4,
        "patient": 3,
        "subject": 3,
        "molecule": 3,
        "community": 2,
        "county": 2,
        "state": 1,
        "date": 2,
        "time": 2,
        "timestamp": 2,
        "longitudinal": 3,
        "temporal": 2,
        "zero": 1,
        "imbalance": 1,
    }
    return sum(weight for keyword, weight in weighted_keywords.items() if keyword in text)


def numeric_feature_matrices(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    target_column: str,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build median-imputed, standardized numeric feature matrices."""
    feature_columns = [column for column in train.columns if column != target_column]
    train_numeric = train[feature_columns].apply(pd.to_numeric, errors="coerce")
    test_numeric = test[feature_columns].apply(pd.to_numeric, errors="coerce")
    keep_columns = [
        column
        for column in feature_columns
        if not train_numeric[column].isna().all() or not test_numeric[column].isna().all()
    ]
    if not keep_columns:
        return (
            np.empty((len(train), 0), dtype=float),
            np.empty((len(test), 0), dtype=float),
            [],
        )
    train_numeric = train_numeric[keep_columns]
    test_numeric = test_numeric[keep_columns]
    medians = train_numeric.median(axis=0, skipna=True).fillna(0.0)
    train_filled = train_numeric.fillna(medians).to_numpy(dtype=float)
    test_filled = test_numeric.fillna(medians).to_numpy(dtype=float)
    means = train_filled.mean(axis=0)
    stds = train_filled.std(axis=0)
    stds[stds == 0.0] = 1.0
    return (train_filled - means) / stds, (test_filled - means) / stds, keep_columns


def majority_label(labels: list[str]) -> str:
    """Return the majority normalized label with deterministic tie-breaking."""
    counts = Counter(labels)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def classification_accuracy(y_true: list[str], y_pred: list[str]) -> float:
    """Compute exact-match classification accuracy."""
    if len(y_true) == 0:
        return float("nan")
    return sum(true == pred for true, pred in zip(y_true, y_pred, strict=True)) / len(
        y_true
    )


def knn_classify(
    train_x: np.ndarray,
    train_y: list[str],
    test_x: np.ndarray,
    *,
    k: int,
    max_train_rows: int,
    seed: int,
) -> list[str]:
    """Run a small numeric-only kNN classifier."""
    if train_x.shape[1] == 0:
        return [majority_label(train_y)] * len(test_x)
    if len(train_x) > max_train_rows:
        rng = np.random.default_rng(seed)
        subset = np.sort(rng.choice(len(train_x), size=max_train_rows, replace=False))
        train_x = train_x[subset]
        train_y = [train_y[index] for index in subset]
    k = max(1, min(k, len(train_x)))
    train_sq = np.sum(train_x * train_x, axis=1)
    predictions: list[str] = []
    for start in range(0, len(test_x), 512):
        chunk = test_x[start : start + 512]
        distances = np.sum(chunk * chunk, axis=1, keepdims=True) + train_sq[None, :]
        distances -= 2.0 * chunk @ train_x.T
        nearest = np.argpartition(distances, kth=k - 1, axis=1)[:, :k]
        for neighbor_indices in nearest:
            labels = [train_y[index] for index in neighbor_indices]
            predictions.append(majority_label(labels))
    return predictions


def regression_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute RMSE."""
    if len(y_true) == 0:
        return float("nan")
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def ridge_regression_predict(
    train_x: np.ndarray,
    train_y: np.ndarray,
    test_x: np.ndarray,
    *,
    alpha: float = 1.0,
) -> np.ndarray:
    """Fit a small numeric-only ridge regression and predict test rows."""
    if train_x.shape[1] == 0:
        return np.full(len(test_x), float(train_y.mean()))
    train_design = np.column_stack([np.ones(len(train_x)), train_x])
    test_design = np.column_stack([np.ones(len(test_x)), test_x])
    penalty = np.eye(train_design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(
        train_design.T @ train_design + penalty,
        train_design.T @ train_y,
    )
    return test_design @ coefficients


def rank_classification_task(
    metadata: TaskMetadata,
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    max_train_rows: int,
    seed: int,
) -> tuple[float, float, float, float, float, str, str]:
    """Rank a classification task with majority and numeric-kNN baselines."""
    y_train = [normalize_label(value) for value in train[metadata.target_column]]
    y_test = [normalize_label(value) for value in test[metadata.target_column]]
    dummy_label = majority_label(y_train)
    dummy_pred = [dummy_label] * len(y_test)
    train_x, test_x, feature_columns = numeric_feature_matrices(
        train, test, target_column=metadata.target_column
    )
    easy_pred = knn_classify(
        train_x,
        y_train,
        test_x,
        k=DEFAULT_K,
        max_train_rows=max_train_rows,
        seed=seed,
    )
    dummy_score = classification_accuracy(y_test, dummy_pred)
    easy_score = classification_accuracy(y_test, easy_pred)
    dummy_error = 1.0 - dummy_score
    easy_error = 1.0 - easy_score
    improvement = dummy_error - easy_error
    return (
        dummy_score,
        easy_score,
        dummy_error,
        easy_error,
        improvement,
        f"numeric_{DEFAULT_K}nn",
        f"numeric_features={len(feature_columns)}",
    )


def rank_regression_task(
    metadata: TaskMetadata,
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[float, float, float, float, float, str, str]:
    """Rank a regression task with mean and numeric-ridge baselines."""
    y_train = pd.to_numeric(train[metadata.target_column], errors="coerce")
    y_test = pd.to_numeric(test[metadata.target_column], errors="coerce")
    train_mask = y_train.notna().to_numpy()
    test_mask = y_test.notna().to_numpy()
    if not train_mask.any() or not test_mask.any():
        raise ValueError(
            f"regression target {metadata.target_column!r} has no numeric values"
        )
    train = train.loc[train_mask].reset_index(drop=True)
    test = test.loc[test_mask].reset_index(drop=True)
    y_train_values = y_train.loc[train_mask].to_numpy(dtype=float)
    y_test_values = y_test.loc[test_mask].to_numpy(dtype=float)
    train_x, test_x, feature_columns = numeric_feature_matrices(
        train, test, target_column=metadata.target_column
    )
    dummy_pred = np.full(len(y_test_values), float(y_train_values.mean()))
    easy_pred = ridge_regression_predict(train_x, y_train_values, test_x)
    dummy_score = regression_rmse(y_test_values, dummy_pred)
    easy_score = regression_rmse(y_test_values, easy_pred)
    target_std = float(np.std(y_test_values))
    if target_std == 0.0 or not math.isfinite(target_std):
        target_std = 1.0
    dummy_error = dummy_score / target_std
    easy_error = easy_score / target_std
    improvement = dummy_error - easy_error
    dropped = int((~train_mask).sum() + (~test_mask).sum())
    note = f"numeric_features={len(feature_columns)}"
    if dropped > 0:
        note += f"; dropped_bad_targets={dropped}"
    return (
        dummy_score,
        easy_score,
        dummy_error,
        easy_error,
        improvement,
        "numeric_ridge",
        note,
    )


def rank_task(task_dir: Path, *, max_train_rows: int, seed: int) -> BaselineResult:
    """Rank one generated task directory."""
    metadata = TaskMetadata.model_validate_json(
        (task_dir / "task.json").read_text(encoding="utf-8")
    )
    train = pd.read_csv(task_dir / "train.csv", low_memory=False)
    test = pd.read_csv(task_dir / "test.csv", low_memory=False)
    if metadata.task_type == "classification":
        (
            dummy_score,
            easy_score,
            dummy_error,
            easy_error,
            improvement,
            easy_model,
            notes,
        ) = rank_classification_task(
            metadata, train, test, max_train_rows=max_train_rows, seed=seed
        )
    elif metadata.task_type == "regression":
        (
            dummy_score,
            easy_score,
            dummy_error,
            easy_error,
            improvement,
            easy_model,
            notes,
        ) = rank_regression_task(metadata, train, test)
    else:
        raise ValueError(f"unsupported task_type: {metadata.task_type}")
    return BaselineResult(
        task_id=metadata.task_id,
        name=metadata.name,
        task_type=metadata.task_type,
        metric=metadata.metric,
        n_train_rows=metadata.n_train_rows,
        n_test_rows=metadata.n_test_rows,
        n_features=max(0, len(metadata.columns) - 1),
        dummy_score=dummy_score,
        easy_score=easy_score,
        dummy_normalized_error=dummy_error,
        easy_normalized_error=easy_error,
        improvement=improvement,
        exploit_affordance=exploit_affordance_score(metadata),
        easy_model=easy_model,
        notes=notes,
    )


def write_results(results: list[BaselineResult], output: Path) -> None:
    """Write ranking rows to a TSV file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [result.model_dump() for result in results]
    dataframe = pd.DataFrame(rows)
    dataframe.to_csv(output, sep="\t", index=False)


def main(
    tasks_dir: Annotated[
        Path,
        typer.Option("--tasks-dir", help="Directory containing generated task folders."),
    ] = GENERATED_TASKS_DIR,
    output: Annotated[
        Path,
        typer.Option("--output", help="TSV output path."),
    ] = DEFAULT_OUTPUT,
    max_train_rows: Annotated[
        int,
        typer.Option(
            "--max-train-rows",
            help="Maximum train rows used by the classification kNN baseline.",
        ),
    ] = DEFAULT_MAX_TRAIN_ROWS,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Deterministic seed for any baseline subsampling."),
    ] = 20260407,
) -> None:
    """Rank generated tasks by lowest dumb-to-easy baseline improvement."""
    results: list[BaselineResult] = []
    failures: list[tuple[str, str]] = []
    for task_dir in task_dirs(tasks_dir):
        try:
            results.append(
                rank_task(task_dir, max_train_rows=max_train_rows, seed=seed)
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((task_dir.name, str(exc)))

    results.sort(key=lambda result: (result.improvement, -result.exploit_affordance))
    write_results(results, output)
    typer.echo(f"Wrote {len(results)} ranked tasks to {output}")
    if results:
        typer.echo("Lowest-improvement tasks:")
        for result in results[:10]:
            typer.echo(
                f"  {result.task_id}: improvement={result.improvement:.6f}, "
                f"easy_error={result.easy_normalized_error:.6f}, "
                f"affordance={result.exploit_affordance}"
            )
    if failures:
        typer.echo(f"Failures: {len(failures)}")
        for task_id, message in failures:
            typer.echo(f"  {task_id}: {message}")


if __name__ == "__main__":
    typer.run(main)
