"""EPUB → MOBI via Calibre, with one bounded background queue."""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4


def find_converter(configured: str | None = None) -> str | None:
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
    candidates = [
        shutil.which("ebook-convert"),
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
        "/opt/calibre/ebook-convert",
        str(
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Calibre2/ebook-convert.exe"
        ),
    ]
    return next(
        (
            str(Path(path).resolve())
            for path in candidates
            if path and Path(path).is_file() and os.access(path, os.X_OK)
        ),
        None,
    )


class ConversionQueue:
    def __init__(self, root: Path, executable: str | None):
        self.root = root
        self.executable = executable
        self._lock = Lock()
        self._jobs: dict[str, dict] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kindle-convert")

    def jobs(self) -> list[dict]:
        with self._lock:
            return [job.copy() for job in reversed(list(self._jobs.values()))]

    def submit(self, source: Path) -> dict:
        if not self.executable:
            raise ValueError("未找到 Calibre，请安装后重启服务。")
        relative = source.relative_to(self.root).as_posix()
        destination = source.with_suffix(".mobi")
        with self._lock:
            for job in self._jobs.values():
                if job["source"] == relative and job["status"] in {"queued", "running"}:
                    return job.copy()
            if destination.exists() or destination.is_symlink():
                raise ValueError("同名 MOBI 已存在，为避免覆盖未开始转换。")
            if sum(job["status"] in {"queued", "running"} for job in self._jobs.values()) >= 20:
                raise ValueError("队列已满，请等当前任务完成后再试。")
            # Retain a small recent history; files remain the source of truth after restart.
            for key in list(self._jobs):
                if len(self._jobs) < 100:
                    break
                if self._jobs[key]["status"] not in {"queued", "running"}:
                    del self._jobs[key]
            job = {
                "id": uuid4().hex,
                "source": relative,
                "name": source.name,
                "output": destination.relative_to(self.root).as_posix(),
                "status": "queued",
                "message": "等待转换",
            }
            self._jobs[job["id"]] = job
            snapshot = job.copy()
            self._executor.submit(self._convert, job["id"], source, destination)
            return snapshot

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)

    def _check_source(self, source: Path) -> None:
        relative = source.relative_to(self.root)
        source.resolve(strict=True).relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current = current / part
            if part.startswith(".") or current.is_symlink():
                raise ValueError("源文件已移动或变成符号链接，请刷新列表。")
        if source.suffix.lower() != ".epub" or not source.is_file():
            raise ValueError("请选择文件夹中的 EPUB 文件。")

    def _convert(self, job_id: str, source: Path, destination: Path) -> None:
        self._update(job_id, status="running", message="正在转换，较大的书籍可能需要几分钟")
        try:
            self._check_source(source)
            with TemporaryDirectory(prefix=".kindle-convert-", dir=source.parent) as temporary:
                output = Path(temporary) / "output.mobi"
                with (Path(temporary) / "conversion.log").open("w+b") as log:
                    result = subprocess.run(
                        [
                            self.executable,
                            str(source),
                            str(output),
                            "--output-profile",
                            "kindle",
                            "--mobi-file-type",
                            "old",
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=300,
                        check=False,
                    )
                    if result.returncode:
                        log.seek(max(0, log.tell() - 3000))
                        detail = log.read().decode("utf-8", errors="replace").strip()
                        raise ValueError(
                            detail or f"Calibre 转换失败（退出码 {result.returncode}）"
                        )
                if not output.is_file() or output.stat().st_size == 0:
                    raise ValueError("Calibre 未生成有效的 MOBI 文件。")
                self._check_source(source)
                # Atomic no-clobber publication; partial files never reach the Kindle list.
                os.link(output, destination)
            self._update(job_id, status="done", message="转换完成，已加入 Kindle 下载列表")
        except FileExistsError:
            self._update(job_id, status="failed", message="同名 MOBI 已存在，未覆盖现有文件。")
        except subprocess.TimeoutExpired:
            self._update(
                job_id,
                status="failed",
                message="转换超过 5 分钟，已停止。可在 Calibre 中检查源文件。",
            )
        except (OSError, ValueError, RuntimeError) as error:
            self._update(job_id, status="failed", message=str(error))

    def close(self) -> None:
        self._executor.shutdown(wait=True)
