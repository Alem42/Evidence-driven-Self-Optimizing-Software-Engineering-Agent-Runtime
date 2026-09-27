"""通过已配置的本地服务测试真实生成和人工审核边界。 Live generation/HITL acceptance through an already configured local console."""

import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

TESTS = """package solution

import "testing"

func TestClampAcceptance(t *testing.T) {
	for _, c := range []struct { n, lo, hi, want int; fail bool }{
		{5, 0, 10, 5, false}, {-5, 0, 10, 0, false}, {20, 0, 10, 10, false},
		{0, 0, 10, 0, false}, {10, 0, 10, 10, false}, {1, -3, -3, -3, false},
		{-20, -10, -1, -10, false}, {2, 5, 3, 0, true},
	} {
		got, err := Clamp(c.n, c.lo, c.hi)
		if (err != nil) != c.fail || (!c.fail && got != c.want) {
			t.Fatalf("Clamp(%d,%d,%d)=(%d,%v), want %d fail=%v", c.n, c.lo, c.hi, got, err, c.want, c.fail)
		}
	}
}
"""


def main():
    """两次生成：一份模拟审核后验证，另一份留给用户审核。 Generate twice: validate one reviewed draft and leave another for user review."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--verified-run", help="reuse an already successful run; only generate its feedback draft")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    html = urllib.request.urlopen(base, timeout=10).read().decode()
    token = re.search(r'name="masa-token" content="([^"]+)"', html).group(1)

    def api(path, body=None):
        """使用会话 token 调用本机接口，不处理或导出密钥。 Call the local API without reading or exporting credentials."""
        request = urllib.request.Request(
            base + "/api" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"X-MASA-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)

    assert api("/bootstrap")["capabilities"]["code_generation"]
    settings = api("/settings")
    if not settings["key_configured"]:
        raise SystemExit("Configure a key in the local API settings first.")
    # 明确增大单次输出限制以容纳完整文件；仍只生成两次。
    # Allow a complete file within a bounded output limit; only two generation requests follow.
    api(
        "/settings",
        {"id": settings["active_id"], "max_output_tokens": 4096, "timeout_seconds": 60},
    )
    goal = "实现 Go 函数 Clamp(value, min, max int) (int, error)。value 限制在闭区间内；min 大于 max 时返回错误。支持负数、相等边界和整数边界。保留 package solution，只使用标准库，添加中英文函数说明。"
    if args.verified_run:
        first = args.verified_run
        detail = api("/runs/"+first)
        assert detail["run"]["status"] == "succeeded"
        approved = api("/runs/"+first+"/artifacts/"+detail["run"]["data"]["codegen"]["approval_ref"])["artifact"]
        assert (Path(detail["run"]["data"]["workspace"])/"solution.go").read_text(encoding="utf-8") == approved["files"][0]["content"]
        assert (Path(detail["run"]["data"]["source"])/"solution.go").read_text(encoding="utf-8") == "package solution\n"
    else:
        first = api("/generate", {"goal": goal, "tests": TESTS})["id"]
        detail = api("/runs/" + first)
        ref = detail["run"]["data"]["codegen"]["proposal_ref"]
        draft = api("/runs/" + first + "/artifacts/" + ref)["artifact"]
        before = (Path(detail["run"]["data"]["workspace"]) / "solution.go").read_text(encoding="utf-8")
        assert before == "package solution\n" and not detail["tools"]
        for path, body in [
            ("/runs/" + first + "/resume", {}),
            (
                "/runs/" + first + "/review-code",
                {
                    "action": "approve",
                    "proposal_ref": "0" * 64,
                    "content": draft["content"],
                },
            ),
        ]:
            try:
                api(path, body)
                raise AssertionError("Review guard unexpectedly allowed execution")
            except urllib.error.HTTPError as error:
                assert error.code == 400
                error.close()
        edited = "// Reviewed during automated HITL acceptance.\n" + draft["content"]
        api(
            "/runs/" + first + "/review-code",
            {"action": "approve", "proposal_ref": ref, "content": edited},
        )
        deadline = time.monotonic() + 180
        while True:
            detail = api("/runs/" + first)
            if not detail["active"]:
                break
            assert time.monotonic() < deadline, "verification deadline exceeded"
            time.sleep(0.5)
        assert detail["run"]["status"] == "succeeded", detail["run"]["reason"]
        assert (
            Path(detail["run"]["data"]["workspace"]) / "solution.go"
        ).read_text(encoding="utf-8") == edited
        assert (Path(detail["run"]["data"]["source"]) / "solution.go").read_text(encoding="utf-8") == before
    second = api(
        "/generate",
        {
            "goal": goal + "\n追加要求：在注释中给出两个调用示例，函数签名和行为不变。",
            "repo": detail["run"]["data"]["workspace"],
            "target": "solution.go",
            "parent_run_id": first,
        },
    )["id"]
    pending = api("/runs/" + second)
    assert pending["run"]["data"]["codegen"]["status"] == "awaiting_review"
    assert not pending["tools"]
    results = {
        "verified_run": first,
        "pending_human_review": second,
        "verified_status": detail["run"]["status"],
        "model_calls": detail["run"]["model_calls"],
        "tool_calls": detail["run"]["tool_calls"],
        "usage": [
            e["payload"]["usage"]
            for d in (detail, pending)
            for e in d["events"]
            if e["type"] == "model_completed" and e["payload"].get("usage")
        ],
    }
    root = Path(__file__).resolve().parents[1]
    (root / ".masa/codegen_smoke.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
