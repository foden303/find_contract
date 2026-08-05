"""Local-only web UI: pick a raw Excel/CSV file, map the columns, scan.

Binds to 127.0.0.1 by design — this is a single-user tool on a personal
machine, so there is no auth layer and none should be added without also
adding one before exposing the port.
"""

import asyncio
import csv
import io
import json
import os
import time
import uuid
from dataclasses import asdict

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from core.cache import Cache
from core.config import Config, data_dir
from core.models import Result
from core.pipeline import process_batch
from core.store import Store
from core.tabular import (
    UnsupportedFile,
    build_jobs,
    guess_columns,
    load_table,
    preview_table,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.getenv("FINDER_UPLOAD_DIR") or os.path.join(ROOT, ".uploads")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
RUNS_DB = os.path.join(data_dir(), ".runs.db")
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="B2B Contact Finder", docs_url=None, redoc_url=None)
store = Store(RUNS_DB)


# --- Live run registry -----------------------------------------------------


class RunHandle:
    """Keeps a running scan's task and its SSE subscribers together."""

    def __init__(self, run_id: str, total: int):
        self.run_id = run_id
        self.total = total
        self.task: asyncio.Task | None = None
        self.subscribers: list[asyncio.Queue] = []
        self.finished = False

    def publish(self, event: dict) -> None:
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)


RUNS: dict[str, RunHandle] = {}


# --- Request models --------------------------------------------------------


class PreviewRequest(BaseModel):
    file_id: str
    sheet: str | None = None


class PlanRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    company_col: str
    country_col: str | None = None
    product_col: str | None = None
    address_col: str | None = None
    dedupe: bool = True
    fuzzy: bool = False
    limit: int | None = None
    product_override: str | None = None


class RunRequest(PlanRequest):
    label: str | None = None
    threads: int = 5
    max_pages: int = 10
    top_results: int = 3
    min_score: float = 30.0
    delay: float = 0.0
    use_cache: bool = True
    guess_emails: bool = True
    early_exit: bool = True
    merge_sources: bool = False
    insecure_tls: bool = False
    use_company_store: bool = True
    search_provider: str = "ddg"
    searxng_url: str | None = None


# --- Helpers ---------------------------------------------------------------


def _upload_path(file_id: str) -> str:
    """Resolve a file id to a path inside the upload directory, or 404."""
    if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(400, "Invalid file id")
    for name in os.listdir(UPLOAD_DIR):
        if name.startswith(file_id + "."):
            return os.path.join(UPLOAD_DIR, name)
    raise HTTPException(404, "Uploaded file not found — please upload it again")


def _split_terms(value: str | None) -> list[str]:
    if not value:
        return []
    return [t.strip() for t in value.replace("\n", ",").split(",") if t.strip()]


def _plan(req: PlanRequest):
    path = _upload_path(req.file_id)
    try:
        headers, rows, _, active = load_table(path, req.sheet)
    except UnsupportedFile as exc:
        raise HTTPException(400, str(exc))
    jobs = build_jobs(
        headers, rows,
        company_col=req.company_col,
        country_col=req.country_col,
        product_col=req.product_col,
        address_col=req.address_col,
        dedupe=req.dedupe,
        fuzzy=req.fuzzy,
        limit=req.limit,
        product_override=_split_terms(req.product_override),
    )
    return jobs, len(rows), active


def _config_from(req: RunRequest) -> Config:
    cfg = Config(
        max_threads_companies=max(1, min(req.threads, 20)),
        max_pages=max(1, min(req.max_pages, 30)),
        top_results=max(1, min(req.top_results, 8)),
        min_score=req.min_score,
        delay=max(0.0, req.delay),
        use_cache=req.use_cache,
        guess_emails=req.guess_emails,
        early_exit=req.early_exit,
        merge_sources=req.merge_sources,
        insecure_tls=req.insecure_tls,
        use_company_store=req.use_company_store,
        search_provider=req.search_provider or "ddg",
    )
    if req.searxng_url:
        cfg.searxng_url = req.searxng_url
    return cfg


RESULT_COLUMNS = [
    ("company", "Company"), ("country", "Country"), ("website", "Website"),
    ("confidence", "Confidence"), ("priority_emails", "Key emails"),
    ("emails", "Other emails"), ("guessed_emails", "Guessed emails"),
    ("phones", "Phones"), ("whatsapp_numbers", "WhatsApp"),
    ("social_links", "Social"), ("match_reason", "Why matched"),
    ("products", "Products"), ("pages_scanned", "Pages scanned"),
    ("address", "Address"), ("address_confirmed", "Address confirmed"),
    ("sources", "Sites scraped"), ("alternates", "Other candidates"),
    ("elapsed", "Seconds"),
]


def _flatten(row: dict) -> list[str]:
    out = []
    for key, _ in RESULT_COLUMNS:
        value = row.get(key)
        if isinstance(value, list):
            out.append(" | ".join(str(v) for v in value))
        elif isinstance(value, float):
            out.append(f"{value:.1f}")
        else:
            out.append("" if value is None else str(value))
    return out


# --- Routes ----------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt"):
        raise HTTPException(400, f"Unsupported file type: {ext or 'unknown'}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File is larger than 64 MB")

    file_id = uuid.uuid4().hex[:12]
    path = os.path.join(UPLOAD_DIR, file_id + ext)
    with open(path, "wb") as f:
        f.write(data)

    try:
        preview = preview_table(path, None)
    except UnsupportedFile as exc:
        os.remove(path)
        raise HTTPException(400, str(exc))
    except Exception as exc:
        os.remove(path)
        raise HTTPException(400, f"Could not read the file: {exc}")

    return {
        "file_id": file_id,
        "filename": file.filename,
        "sheet": preview.sheet,
        "sheets": [asdict(s) for s in preview.sheets],
        "headers": preview.headers,
        "sample": preview.sample,
        "total_rows": preview.total_rows,
        "guess": guess_columns(preview.headers),
    }


@app.post("/api/preview")
async def preview(req: PreviewRequest):
    path = _upload_path(req.file_id)
    try:
        result = preview_table(path, req.sheet)
    except UnsupportedFile as exc:
        raise HTTPException(400, str(exc))
    return {
        "sheet": result.sheet,
        "sheets": [asdict(s) for s in result.sheets],
        "headers": result.headers,
        "sample": result.sample,
        "total_rows": result.total_rows,
        "guess": guess_columns(result.headers),
    }


@app.post("/api/plan")
async def plan(req: PlanRequest):
    jobs, raw_rows, active = _plan(req)
    return {
        "sheet": active,
        "raw_rows": raw_rows,
        "job_count": len(jobs),
        "jobs": [asdict(j) for j in jobs[:25]],
    }


@app.post("/api/run")
async def start_run(req: RunRequest):
    jobs, raw_rows, active = _plan(req)
    if not jobs:
        raise HTTPException(400, "No companies found in that column")

    config = _config_from(req)
    settings = req.model_dump()
    label = req.label or f"{req.company_col} · {len(jobs)} companies"
    run_id = store.create_run(label, f"{active} ({raw_rows} rows)", len(jobs), settings)
    handle = RunHandle(run_id, len(jobs))
    RUNS[run_id] = handle

    async def execute() -> None:
        started = time.perf_counter()
        cache = Cache(config.cache_path, config.cache_ttl, config.use_cache)

        def on_event(event: dict) -> None:
            if event.get("type") == "company_start":
                handle.publish({"type": "start", "query": event["query"]})
            elif event.get("type") == "progress":
                result: Result = event["result"]
                payload = asdict(result)
                store.add_result(run_id, event["index"], result)
                handle.publish({
                    "type": "result",
                    "done": event["done"],
                    "total": event["total"],
                    "result": payload,
                })

        try:
            await process_batch(
                [j.as_row() for j in jobs], config,
                on_event=on_event, cache=cache, store=store,
            )
            elapsed = time.perf_counter() - started
            store.finish_run(run_id, "done", elapsed)
            handle.publish({"type": "done", "elapsed": elapsed})
        except asyncio.CancelledError:
            store.finish_run(run_id, "cancelled", time.perf_counter() - started)
            handle.publish({"type": "cancelled"})
            raise
        except Exception as exc:
            store.finish_run(run_id, "error", time.perf_counter() - started, str(exc))
            handle.publish({"type": "error", "message": str(exc)})
        finally:
            handle.finished = True

    handle.task = asyncio.create_task(execute())
    return {"run_id": run_id, "total": len(jobs)}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    handle = RUNS.get(run_id)
    if handle is None:
        raise HTTPException(404, "Unknown or already-finished run")
    queue = handle.subscribe()

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'total': handle.total})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # stop proxies and browsers timing out
                    if handle.finished and queue.empty():
                        break
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("done", "error", "cancelled"):
                    break
        finally:
            handle.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    handle = RUNS.get(run_id)
    if handle is None or handle.task is None:
        raise HTTPException(404, "Unknown run")
    handle.task.cancel()
    return {"ok": True}


@app.get("/api/runs")
async def list_runs():
    return {"runs": store.list_runs()}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")
    return {"run": run, "results": store.get_results(run_id)}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    store.delete_run(run_id)
    RUNS.pop(run_id, None)
    return {"ok": True}


@app.get("/api/runs/{run_id}/export.csv")
async def export_csv(run_id: str):
    rows = store.get_results(run_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in RESULT_COLUMNS])
    for row in rows:
        writer.writerow(_flatten(row))
    return Response(
        content="﻿" + buf.getvalue(),  # BOM so Excel reads UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="contacts_{run_id}.csv"'},
    )


@app.get("/api/runs/{run_id}/export.xlsx")
async def export_xlsx(run_id: str):
    import openpyxl
    from openpyxl.styles import Font

    rows = store.get_results(run_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append([label for _, label in RESULT_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(_flatten(row))
    ws.freeze_panes = "A2"
    for i, (_, label) in enumerate(RESULT_COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(14, min(40, len(label) + 12))

    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="contacts_{run_id}.xlsx"'},
    )


@app.exception_handler(UnsupportedFile)
async def unsupported_handler(_request, exc: UnsupportedFile):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def serve(host: str | None = None, port: int | None = None) -> None:
    """Start the local server.

    Defaults to 127.0.0.1 because there is no authentication. Inside a
    container it must listen on 0.0.0.0 to be reachable at all — the compose
    file publishes the port only to 127.0.0.1 on the host, which keeps the
    same guarantee.
    """
    import uvicorn

    host = host or os.getenv("FINDER_HOST", "127.0.0.1")
    port = int(port or os.getenv("FINDER_PORT", "8765"))

    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  ! Listening on {host} — this server has no authentication.")
    print(f"\n  B2B Contact Finder -> http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    serve()
