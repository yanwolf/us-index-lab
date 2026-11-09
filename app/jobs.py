"""背景工作（下載、最佳化）：記憶體內的簡單工作表。"""
import threading
import time
import traceback
import uuid

JOBS = {}
_lock = threading.Lock()


def start(kind, fn, label=""):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "label": label, "status": "running", "done": 0, "total": 0,
           "result": None, "error": None, "started": time.time(), "finished": None, "stop": False}
    with _lock:
        JOBS[jid] = job
        # 只留最近 30 筆
        if len(JOBS) > 30:
            for k in sorted(JOBS, key=lambda k: JOBS[k]["started"])[: len(JOBS) - 30]:
                if JOBS[k]["status"] != "running":
                    JOBS.pop(k, None)

    def progress(done, total):
        job["done"], job["total"] = done, total

    def stop_flag():
        return job["stop"]

    def runner():
        try:
            job["result"] = fn(progress, stop_flag)
            job["status"] = "stopped" if job["stop"] else "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = f"{e}"
            job["trace"] = traceback.format_exc()[-2000:]
        job["finished"] = time.time()

    threading.Thread(target=runner, daemon=True).start()
    return job


def public(job, with_result=True):
    out = {k: v for k, v in job.items() if k not in ("result", "trace", "stop")}
    if with_result:
        out["result"] = job["result"]
    return out


def running(kind):
    return any(j["kind"] == kind and j["status"] == "running" for j in JOBS.values())
