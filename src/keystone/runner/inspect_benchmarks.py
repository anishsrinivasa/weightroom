"""Inspect-backed public benchmark adapters.

Agent benchmarks cannot be reduced to a prompt and a string comparison.  This
module runs their maintained Inspect Evals implementations and uses Modal
Sandboxes for the per-task workspaces.  Imports stay inside the entry points so
the API and local unit tests do not need the heavyweight evaluation packages.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from keystone.public_benchmarks import BY_ID, sample_task_ids
from keystone.schema import Status, SuiteResult

SWE_BENCH_VERIFIED_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
INSPECT_AI_VERSION = "0.3.263"
INSPECT_EVALS_VERSION = "0.19.0"
INSPECT_SANDBOXES_VERSION = "0.5.0"
GDPVAL_REVISION = "a3848a2a812d5d4d0f08003fac3c8eac40805962"
HARVEY_LAB_REVISION = "1da4750171bc5a534960b3d82d15ba7fd2cf653f"


@dataclass
class PendingRubricRun:
    suite_id: str
    tasks: list[dict[str, Any]]
    expected_items: int
    started: float
    generation_errors: int = 0


def _numeric_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if 0.0 <= numeric <= 1.0 else None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
        return numeric if 0.0 <= numeric <= 1.0 else None
    return None


def _sample_score(sample: Any, scorer_name: str) -> float | None:
    scores = getattr(sample, "scores", None) or {}
    score = scores.get(scorer_name)
    if score is None and len(scores) == 1:
        score = next(iter(scores.values()))
    return _numeric_score(getattr(score, "value", None)) if score is not None else None


def result_from_inspect_logs(
    suite_id: str,
    logs: Iterable[Any],
    *,
    scorer_name: str,
    expected_items: int,
    duration_s: float,
) -> SuiteResult:
    """Convert Inspect's logs into the marketplace's fail-closed result."""
    samples = [sample for log in logs for sample in (getattr(log, "samples", None) or [])]
    values = [
        value
        for sample in samples
        if (value := _sample_score(sample, scorer_name)) is not None
    ]
    errors = len(samples) - len(values)
    benchmark = BY_ID[suite_id]
    if len(samples) != expected_items or errors:
        return SuiteResult(
            suite_id=suite_id,
            suite_version=benchmark.version,
            display_name=benchmark.display_name,
            status=Status.ERROR,
            diagnostic=True,
            n_items=len(values),
            duration_s=round(duration_s, 2),
            metrics={
                "completed_items": float(len(values)),
                "errored_items": float(errors + max(0, expected_items - len(samples))),
            },
            error=(
                f"Inspect completed {len(values)} of {expected_items} required tasks; "
                "the benchmark is invalid until every sampled task is scored"
            ),
        )

    mean = sum(values) / len(values)
    return SuiteResult(
        suite_id=suite_id,
        suite_version=benchmark.version,
        display_name=benchmark.display_name,
        status=Status.PASS,
        score=mean,
        n_items=len(values),
        duration_s=round(duration_s, 2),
        metrics={
            "resolved_rate": mean,
            "resolved_items": float(sum(value == 1.0 for value in values)),
            "errored_items": 0.0,
        },
    )


def run_swe_bench_verified(
    *,
    served_model_name: str,
    artifact_digest: str,
    log_dir: Path,
    max_samples: int = 8,
) -> SuiteResult:
    """Run a deterministic 100-task SWE-bench Verified sample.

    Inspect controls the agent loop.  Every task gets an isolated, networkless
    Modal Sandbox built from the published SWE-bench image, and the official
    repository tests determine whether the patch resolves the issue.
    """
    # Importing the provider registers ``sandbox="modal"`` with Inspect.
    import inspect_sandboxes.modal  # noqa: F401
    from inspect_ai import eval as inspect_eval
    from inspect_evals.swe_bench import swe_bench

    started = time.monotonic()
    benchmark = BY_ID["swe_bench_verified"]
    task = swe_bench(
        sandbox_type="modal",
        allow_internet=False,
        revision=SWE_BENCH_VERIFIED_REVISION,
        # inspect-evals 0.19.0 still emits the legacy
        # ``x-inspect_modal_sandbox`` extension. inspect-sandboxes 0.5.0 uses
        # ``x-modal`` and also understands Docker's network_mode, so supply the
        # compatible spec explicitly rather than silently losing isolation.
        sandbox_config=_swe_modal_sandbox_spec,
    )
    available_ids = [str(sample.id) for sample in task.dataset]
    chosen = sample_task_ids(
        available_ids,
        artifact_digest=artifact_digest,
        suite_id=benchmark.suite_id,
    )
    logs = inspect_eval(
        task,
        model=f"openai-api/keystone/{served_model_name}",
        model_base_url="http://127.0.0.1:8000/v1",
        model_args={
            "api_key": "not-used",
            # Many uploaded chat models do not expose native function calling.
            # Inspect's emulation keeps the same ReAct tool contract portable.
            "emulate_tools": True,
            "responses_api": False,
        },
        sample_id=chosen,
        max_samples=max_samples,
        max_sandboxes=max_samples,
        fail_on_error=False,
        continue_on_fail=True,
        retry_on_error=1,
        sandbox_cleanup=True,
        log_dir=str(log_dir),
        display="plain",
    )
    return result_from_inspect_logs(
        benchmark.suite_id,
        logs,
        scorer_name="swe_bench_scorer",
        expected_items=len(chosen),
        duration_s=time.monotonic() - started,
    )


def _swe_modal_sandbox_spec(sandbox_type: str, sample: Any):
    from inspect_ai.util import SandboxEnvironmentSpec

    metadata = sample.metadata or {}
    image_name = metadata["image_name"]
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "-", str(sample.id))
    config = Path("/tmp/inspect-config/swe-bench") / f"{safe_id}-compose.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            [
                "services:",
                "  default:",
                f"    image: {image_name}",
                "    command: sleep infinity",
                "    working_dir: /testbed",
                "    network_mode: none",
                "x-modal:",
                "  timeout: 14400",
                "  block_network: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return SandboxEnvironmentSpec(type=sandbox_type, config=str(config))


def _modal_compose_for_dockerfile(dockerfile: Path, output: Path) -> Path:
    """Make Inspect's Modal provider build a Dockerfile with runtime egress off."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(
            [
                "services:",
                "  default:",
                "    build:",
                f"      context: {dockerfile.parent}",
                f"      dockerfile: {dockerfile.name}",
                "    command: sleep infinity",
                "    working_dir: /workspace",
                "    network_mode: none",
                "x-modal:",
                "  timeout: 14400",
                "  block_network: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return output


def _inspect_model_args(served_model_name: str) -> dict[str, Any]:
    return {
        "model": f"openai-api/keystone/{served_model_name}",
        "model_base_url": "http://127.0.0.1:8000/v1",
        "model_args": {
            "api_key": "not-used",
            "emulate_tools": True,
            "responses_api": False,
        },
    }


def smoke_agent_sandboxes(*, harvey_root: Path) -> dict[str, str]:
    """Build and execute every agent-benchmark sandbox without a model."""
    import inspect_sandboxes.modal  # noqa: F401
    from inspect_ai import Task
    from inspect_ai import eval as inspect_eval
    from inspect_ai.dataset import Sample
    from inspect_ai.solver import Generate, Solver, TaskState, solver
    from inspect_ai.util import SandboxEnvironmentSpec, sandbox
    import inspect_evals.gdpval as gdpval_package

    @solver
    def sandbox_probe(required_dir: str) -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            result = await sandbox().exec(
                ["sh", "-lc", f"python --version && test -d {required_dir}"],
                timeout=120,
            )
            if not result.success:
                raise RuntimeError(result.stderr or "sandbox probe failed")
            return state

        return solve

    dockerfiles = {
        "gdpval": Path(gdpval_package.__file__).parent / "Dockerfile",
        "harvey_lab": harvey_root / "sandbox" / "Dockerfile",
    }
    cases: dict[str, tuple[SandboxEnvironmentSpec, str]] = {
        suite_id: (
            SandboxEnvironmentSpec(
                type="modal",
                config=str(
                    _modal_compose_for_dockerfile(
                        dockerfile,
                        Path("/tmp/inspect-config/smoke")
                        / f"{suite_id}-compose.yaml",
                    )
                ),
            ),
            "/workspace",
        )
        for suite_id, dockerfile in dockerfiles.items()
    }
    # This instance is the maintained Inspect adapter's own documented example.
    # Pulling its real Epoch image proves registry, architecture, /testbed, and
    # network-isolation behavior before a paid 100-task run begins.
    swe_sample = Sample(
        id="django__django-11039",
        input="sandbox smoke test",
        target="NONE",
        metadata={
            "image_name": (
                "ghcr.io/epoch-research/"
                "swe-bench.eval.x86_64.django__django-11039:latest"
            )
        },
    )
    cases["swe_bench_verified"] = (
        _swe_modal_sandbox_spec("modal", swe_sample),
        "/testbed",
    )

    outcomes: dict[str, str] = {}
    for suite_id, (sandbox_spec, required_dir) in cases.items():
        task = Task(
            dataset=[Sample(id=suite_id, input="sandbox smoke test", target="NONE")],
            solver=sandbox_probe(required_dir),
            sandbox=sandbox_spec,
        )
        logs = inspect_eval(
            task,
            model="mockllm/model",
            score=False,
            fail_on_error=True,
            sandbox_cleanup=True,
            log_dir=f"/tmp/inspect-logs/smoke/{suite_id}",
            display="plain",
        )
        if len(logs) != 1 or str(logs[0].status).lower().endswith("error"):
            raise RuntimeError(f"{suite_id} sandbox smoke test did not complete")
        outcomes[suite_id] = "ok"
    return outcomes


def run_gdpval_generation(
    *,
    served_model_name: str,
    artifact_digest: str,
    dataset_root: Path,
    log_dir: Path,
    max_samples: int = 4,
) -> PendingRubricRun:
    """Generate GDPval work products in the maintained Inspect sandbox."""
    import inspect_sandboxes.modal  # noqa: F401
    from datasets import load_dataset
    from inspect_ai import eval as inspect_eval
    from inspect_ai.util import SandboxEnvironmentSpec
    from inspect_evals.gdpval import gdpval
    import inspect_evals.gdpval as gdpval_package

    started = time.monotonic()
    benchmark = BY_ID["gdpval"]
    task = gdpval(upload_to_hf=False)
    available_ids = [str(sample.id) for sample in task.dataset]
    chosen = sample_task_ids(
        available_ids,
        artifact_digest=artifact_digest,
        suite_id=benchmark.suite_id,
    )

    raw_rows = load_dataset(
        "openai/gdpval",
        split="train",
        revision=GDPVAL_REVISION,
    )
    raw_by_id = {str(row["task_id"]): dict(row) for row in raw_rows}
    chosen_set = set(chosen)

    # Inspect's public task points at remote reference-file URLs. Certification
    # runs with egress disabled, so replace them with the pinned local snapshot.
    for sample in task.dataset:
        task_id = str(sample.id)
        if task_id not in chosen_set:
            continue
        row = raw_by_id[task_id]
        sample.files = {
            f"reference_files/{Path(relative).name}": str(dataset_root / relative)
            for relative in row["reference_files"]
        }

    dockerfile = Path(gdpval_package.__file__).parent / "Dockerfile"
    compose = _modal_compose_for_dockerfile(
        dockerfile,
        Path("/tmp/inspect-config/gdpval-compose.yaml"),
    )
    task.sandbox = SandboxEnvironmentSpec(type="modal", config=str(compose))

    logs = inspect_eval(
        task,
        **_inspect_model_args(served_model_name),
        sample_id=chosen,
        max_samples=max_samples,
        max_sandboxes=max_samples,
        fail_on_error=False,
        continue_on_fail=True,
        retry_on_error=1,
        sandbox_cleanup=True,
        score=False,
        log_dir=str(log_dir),
        display="plain",
    )

    returned = {
        str(sample.id): sample
        for log in logs
        for sample in (getattr(log, "samples", None) or [])
    }
    pending_tasks: list[dict[str, Any]] = []
    errors = 0
    for task_id in chosen:
        sample = returned.get(task_id)
        store = getattr(sample, "store", None) if sample is not None else None
        if not isinstance(store, dict) or not store:
            errors += 1
            continue
        row = raw_by_id[task_id]
        pending_tasks.append(
            {
                "id": task_id,
                "prompt": row["prompt"],
                # The public GDPval release does not include expert rubrics or
                # gold deliverables.  These fixed criteria therefore produce a
                # transparent prompt-compliance proxy, not the canonical score.
                "rubric": [
                    {
                        "rubric_item_id": "deliverable",
                        "score": 1,
                        "criterion": "The requested deliverable exists, is readable, and is substantive.",
                    },
                    {
                        "rubric_item_id": "requirements",
                        "score": 2,
                        "criterion": "The work product addresses every explicit requirement in the task prompt.",
                    },
                    {
                        "rubric_item_id": "evidence",
                        "score": 2,
                        "criterion": "Claims, calculations, and extracted facts are supported by the supplied source files.",
                    },
                    {
                        "rubric_item_id": "professional_quality",
                        "score": 1,
                        "criterion": "The deliverable is professionally structured and usable for its stated occupation.",
                    },
                ],
                "candidate_files": dict(store),
                "reference_files": [
                    str(dataset_root / relative)
                    for relative in row["reference_files"]
                ],
            }
        )

    return PendingRubricRun(
        suite_id=benchmark.suite_id,
        tasks=pending_tasks,
        expected_items=len(chosen),
        started=started,
        generation_errors=errors,
    )


def _extract_sandbox_files(directory: str):
    """Return an Inspect solver that copies one output tree into Sample.store."""
    from inspect_ai.solver import Generate, Solver, TaskState, solver
    from inspect_ai.util import sandbox, store

    @solver
    def extract_output_files() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            check = await sandbox().exec(["test", "-d", directory])
            if not check.success:
                return state
            listed = await sandbox().exec(
                ["find", directory, "-type", "f", "-print0"], timeout=120
            )
            if not listed.success:
                return state
            for file_path in (value for value in listed.stdout.split("\0") if value):
                read = await sandbox().exec(
                    ["base64", "-w", "0", file_path], timeout=120
                )
                if read.success:
                    key = file_path.removeprefix(f"{directory}/")
                    store().set(key, read.stdout)
            return state

        return solve

    return extract_output_files()


def run_harvey_lab_generation(
    *,
    served_model_name: str,
    artifact_digest: str,
    dataset_root: Path,
    log_dir: Path,
    max_samples: int = 4,
) -> PendingRubricRun:
    """Run 100 pinned Harvey LAB tasks with its files and public rubrics."""
    import inspect_sandboxes.modal  # noqa: F401
    from inspect_ai import Task
    from inspect_ai import eval as inspect_eval
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import exact
    from inspect_ai.solver import generate, use_tools
    from inspect_ai.tool import bash, python
    from inspect_ai.util import SandboxEnvironmentSpec

    started = time.monotonic()
    benchmark = BY_ID["harvey_lab"]
    task_files = sorted((dataset_root / "tasks").rglob("task.json"))
    available_ids = [
        task_file.parent.relative_to(dataset_root / "tasks").as_posix()
        for task_file in task_files
    ]
    chosen = sample_task_ids(
        available_ids,
        artifact_digest=artifact_digest,
        suite_id=benchmark.suite_id,
    )
    task_file_by_id = dict(zip(available_ids, task_files))
    rows: dict[str, dict[str, Any]] = {}
    samples: list[Sample] = []
    for task_id in chosen:
        task_file = task_file_by_id[task_id]
        row = json.loads(task_file.read_text(encoding="utf-8"))
        rows[task_id] = row
        documents = task_file.parent / "documents"
        files = {
            f"documents/{path.relative_to(documents).as_posix()}": str(path)
            for path in sorted(documents.rglob("*"))
            if path.is_file()
        }
        deliverables = ", ".join(sorted(row.get("deliverables", {})))
        prompt = (
            f"{row.get('title', 'Legal work product')}\n\n{row['instructions']}\n\n"
            "Source matter files are in the documents/ directory. Use the shell "
            "or Python tools to inspect them. Save the completed work product(s) "
            f"under output/. Required deliverables: {deliverables}."
        )
        samples.append(
            Sample(
                id=task_id,
                input=prompt,
                target="NONE",
                files=files,
                setup="mkdir -p output",
            )
        )

    compose = _modal_compose_for_dockerfile(
        dataset_root / "sandbox" / "Dockerfile",
        Path("/tmp/inspect-config/harvey-lab-compose.yaml"),
    )
    task = Task(
        dataset=MemoryDataset(samples, name="harvey-lab"),
        solver=[
            use_tools([bash(300), python(300)]),
            generate(tool_calls="loop", max_tokens=8_192),
            _extract_sandbox_files("output"),
        ],
        scorer=exact(),
        sandbox=SandboxEnvironmentSpec(type="modal", config=str(compose)),
        version=HARVEY_LAB_REVISION,
        message_limit=100,
        time_limit=3_600,
    )
    logs = inspect_eval(
        task,
        **_inspect_model_args(served_model_name),
        max_samples=max_samples,
        max_sandboxes=max_samples,
        fail_on_error=False,
        continue_on_fail=True,
        retry_on_error=1,
        sandbox_cleanup=True,
        score=False,
        log_dir=str(log_dir),
        display="plain",
    )
    returned = {
        str(sample.id): sample
        for log in logs
        for sample in (getattr(log, "samples", None) or [])
    }
    pending_tasks: list[dict[str, Any]] = []
    errors = 0
    for task_id in chosen:
        sample = returned.get(task_id)
        output_store = getattr(sample, "store", None) if sample is not None else None
        if not isinstance(output_store, dict) or not output_store:
            errors += 1
            continue
        row = rows[task_id]
        pending_tasks.append(
            {
                "id": task_id,
                "prompt": row["instructions"],
                "rubric": row["criteria"],
                "candidate_files": dict(output_store),
                "reference_files": [],
            }
        )
    return PendingRubricRun(
        suite_id=benchmark.suite_id,
        tasks=pending_tasks,
        expected_items=len(chosen),
        started=started,
        generation_errors=errors,
    )


def _extract_document(name: str, payload: bytes) -> str:
    """Extract judgeable text from common GDPval/Harvey deliverables."""
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(payload)).pages)
        if suffix == ".docx":
            from docx import Document

            document = Document(io.BytesIO(payload))
            paragraphs = [paragraph.text for paragraph in document.paragraphs]
            tables = [
                " | ".join(cell.text for cell in row.cells)
                for table in document.tables
                for row in table.rows
            ]
            return "\n".join(paragraphs + tables)
        if suffix in {".xlsx", ".xlsm"}:
            from openpyxl import load_workbook

            workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
            lines: list[str] = []
            for worksheet in workbook.worksheets:
                lines.append(f"# Sheet: {worksheet.title}")
                for row in worksheet.iter_rows(values_only=True):
                    lines.append(" | ".join("" if value is None else str(value) for value in row))
            return "\n".join(lines)
        if suffix == ".pptx":
            from pptx import Presentation

            presentation = Presentation(io.BytesIO(payload))
            return "\n".join(
                shape.text
                for slide in presentation.slides
                for shape in slide.shapes
                if hasattr(shape, "text") and shape.text
            )
        return payload.decode("utf-8", errors="replace")
    except Exception as exc:  # malformed output is evidence, not a harness crash
        return f"[Could not parse {name}: {type(exc).__name__}: {exc}]"


def _file_bundle(candidate_files: dict[str, str], reference_files: list[str]) -> tuple[str, str]:
    candidate_parts = []
    for name, encoded in sorted(candidate_files.items()):
        try:
            payload = base64.b64decode(encoded, validate=True)
        except Exception:
            payload = b""
        candidate_parts.append(f"## {name}\n{_extract_document(name, payload)}")

    reference_parts = []
    for value in reference_files:
        path = Path(value)
        try:
            reference_parts.append(
                f"## {path.name}\n{_extract_document(path.name, path.read_bytes())}"
            )
        except OSError as exc:
            reference_parts.append(f"## {path.name}\n[Missing reference: {exc}]")
    # Bound judge context deterministically. Filenames and the beginning of
    # every extracted bundle remain visible; gigantic workbooks cannot OOM it.
    return "\n\n".join(candidate_parts)[:24_000], "\n\n".join(reference_parts)[:24_000]


def _json_object(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    candidates = [fenced.group(1)] if fenced else []
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


async def score_rubric_run(run: PendingRubricRun, judge) -> SuiteResult:
    """Score generated artifacts with a separate pinned open-weight judge.

    GDPval is a prompt-compliance proxy because its canonical expert rubric and
    gold deliverables are not public. Harvey uses its public criteria and
    official all-pass aggregation, but substitutes a pinned open-weight judge.
    Both substitutions are recorded in the benchmark version and metrics.
    """
    benchmark = BY_ID[run.suite_id]
    if run.generation_errors or len(run.tasks) != run.expected_items:
        return SuiteResult(
            suite_id=run.suite_id,
            suite_version=benchmark.version,
            display_name=benchmark.display_name,
            status=Status.ERROR,
            diagnostic=True,
            n_items=len(run.tasks),
            duration_s=round(time.monotonic() - run.started, 2),
            metrics={"generation_errors": float(run.generation_errors)},
            error=(
                f"agent produced complete artifacts for {len(run.tasks)} of "
                f"{run.expected_items} required tasks"
            ),
        )

    semaphore = asyncio.Semaphore(8)

    async def grade(task: dict[str, Any]) -> dict[str, float] | None:
        candidate, reference = _file_bundle(
            task["candidate_files"], task["reference_files"]
        )
        if run.suite_id == "harvey_lab":
            criteria = [
                {
                    "id": str(item.get("id", index)),
                    "points": 1.0,
                    "criterion": (
                        f"{item.get('title', '')}\n{item.get('match_criteria', '')}"
                    ),
                    "deliverables": list(item.get("deliverables", [])),
                }
                for index, item in enumerate(task["rubric"])
            ]
        else:
            criteria = [
                {
                    "id": str(item.get("rubric_item_id", index)),
                    "points": float(item.get("score", 0)),
                    "criterion": item.get("criterion", ""),
                }
                for index, item in enumerate(task["rubric"])
                if float(item.get("score", 0)) > 0
            ]
        if not criteria:
            return None
        prompt = (
            "You are an independent work-product evaluator. Compare the candidate "
            "deliverables with the task, evaluation criteria, and supplied source "
            "materials when present. "
            "Evaluate substance, correctness, required structure, and file contents. "
            "Return JSON only as {\"items\":[{\"id\":\"...\",\"pass\":true}]}; "
            "include every supplied criterion exactly once.\n\n"
            f"TASK\n{task['prompt']}\n\nRUBRIC\n{json.dumps(criteria)}\n\n"
            f"CANDIDATE\n{candidate}\n\nSOURCE MATERIALS\n{reference}"
        )
        async with semaphore:
            response = await judge.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.0,
            )
        parsed = _json_object(response)
        if parsed is None or not isinstance(parsed.get("items"), list):
            return None
        verdicts = {
            str(item.get("id")): item.get("pass")
            for item in parsed["items"]
            if isinstance(item, dict) and isinstance(item.get("pass"), bool)
        }
        if set(verdicts) != {criterion["id"] for criterion in criteria}:
            return None
        possible = sum(criterion["points"] for criterion in criteria)
        earned = sum(
            criterion["points"]
            for criterion in criteria
            if verdicts[criterion["id"]]
        )
        if not possible:
            return None
        passed = sum(bool(verdicts[criterion["id"]]) for criterion in criteria)
        weighted = earned / possible
        return {
            "score": float(passed == len(criteria))
            if run.suite_id == "harvey_lab"
            else weighted,
            "criterion_passed": float(passed),
            "criterion_total": float(len(criteria)),
            "weighted_fraction": weighted,
        }

    values = await asyncio.gather(*(grade(task) for task in run.tasks))
    parsed_values = [value for value in values if value is not None]
    if len(parsed_values) != run.expected_items:
        return SuiteResult(
            suite_id=run.suite_id,
            suite_version=benchmark.version,
            display_name=benchmark.display_name,
            status=Status.ERROR,
            diagnostic=True,
            n_items=len(parsed_values),
            duration_s=round(time.monotonic() - run.started, 2),
            metrics={"judge_parse_rate": len(parsed_values) / run.expected_items},
            error=(
                f"judge returned valid complete rubric decisions for "
                f"{len(parsed_values)} of {run.expected_items} tasks"
            ),
        )

    score = sum(value["score"] for value in parsed_values) / len(parsed_values)
    criteria_passed = sum(value["criterion_passed"] for value in parsed_values)
    criteria_total = sum(value["criterion_total"] for value in parsed_values)
    metrics = {
        "judge_parse_rate": 1.0,
        "automated_rubric_proxy": 1.0,
    }
    if run.suite_id == "harvey_lab":
        metrics.update(
            {
                "all_pass_rate": score,
                "criterion_pass_rate": criteria_passed / criteria_total,
                "open_weight_judge_proxy": 1.0,
            }
        )
    else:
        metrics.update(
            {
                "prompt_compliance_estimate": score,
                "canonical_expert_score_available": 0.0,
            }
        )
    return SuiteResult(
        suite_id=run.suite_id,
        suite_version=benchmark.version,
        display_name=benchmark.display_name,
        status=Status.PASS,
        score=score,
        n_items=len(parsed_values),
        duration_s=round(time.monotonic() - run.started, 2),
        metrics=metrics,
    )
