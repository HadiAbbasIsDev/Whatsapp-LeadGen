import json
from pathlib import Path

from decor_matcher.cli import main
from decor_matcher.types import MatchResult


class MatchingStub:
    def match(self, path: Path) -> MatchResult:
        return MatchResult(
            decision="catalog_match",
            product_id="42",
            product_name="Aura Chair",
            confidence=0.94,
            reason="accepted",
            evidence=("sscd", "lightglue", "ransac"),
            experimental=True,
        )


class ErrorStub:
    def match(self, path: Path) -> MatchResult:
        return MatchResult("error", None, None, None, "invalid_query_image", (), True)


def test_help_lists_every_mvp_command(capsys):
    exit_code = main(["--help"])

    output = capsys.readouterr().out
    assert exit_code == 0
    for command in ("build", "match", "ui", "make-fixtures", "benchmark"):
        assert command in output


def test_match_cli_prints_one_json_object(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("decor_matcher.cli.make_matcher", lambda _: MatchingStub())

    exit_code = main(["match", str(tmp_path / "query.jpg"), "--runtime", str(tmp_path / "runtime")])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert exit_code == 0
    assert output["decision"] == "catalog_match"
    assert output["product_id"] == "42"
    assert output["evidence"] == ["sscd", "lightglue", "ransac"]


def test_match_cli_returns_one_for_operational_error(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("decor_matcher.cli.make_matcher", lambda _: ErrorStub())

    exit_code = main(["match", str(tmp_path / "broken.jpg")])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["decision"] == "error"


def test_build_cli_rejects_any_limit_other_than_100(capsys, tmp_path):
    exit_code = main(["build", "--catalog", str(tmp_path / "products.json"), "--limit", "99"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error"
    assert "exactly 100" in output["reason"]


def test_ui_cli_starts_only_on_loopback(capsys, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "decor_matcher.cli.serve_ui",
        lambda runtime, port: calls.append((runtime, port)),
    )

    exit_code = main(["ui", "--runtime", str(tmp_path / "runtime"), "--port", "8765"])

    assert exit_code == 0
    assert calls == [(tmp_path / "runtime", 8765)]
    output = json.loads(capsys.readouterr().out)
    assert output == {"host": "127.0.0.1", "port": 8765, "status": "serving"}


def test_benchmark_cli_passes_explicit_negative_bound(capsys, monkeypatch, tmp_path):
    calls = []

    def fake_benchmark(runtime, fixtures, catalog, max_negatives):
        calls.append((runtime, fixtures, catalog, max_negatives))
        return {
            "status": "ok",
            "indexed_references": 100,
            "positive_queries": 5,
            "negative_queries": max_negatives,
        }

    monkeypatch.setattr("decor_matcher.cli.benchmark_runtime", fake_benchmark)
    runtime = tmp_path / "runtime"
    fixtures = tmp_path / "fixtures"
    catalog = tmp_path / "products.json"

    exit_code = main(
        [
            "benchmark",
            "--runtime",
            str(runtime),
            "--fixtures",
            str(fixtures),
            "--catalog",
            str(catalog),
            "--max-negatives",
            "3",
        ]
    )

    assert exit_code == 0
    assert calls == [(runtime, fixtures, catalog, 3)]
    assert json.loads(capsys.readouterr().out)["negative_queries"] == 3


def test_benchmark_cli_rejects_negative_bound_below_zero(capsys, tmp_path):
    exit_code = main(
        [
            "benchmark",
            "--runtime",
            str(tmp_path / "runtime"),
            "--fixtures",
            str(tmp_path / "fixtures"),
            "--catalog",
            str(tmp_path / "products.json"),
            "--max-negatives",
            "-1",
        ]
    )

    assert exit_code == 1
    assert "non-negative" in json.loads(capsys.readouterr().out)["reason"]


def test_command_exception_is_one_json_error(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "decor_matcher.cli.generate_fixtures_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private details")),
    )

    exit_code = main(
        [
            "make-fixtures",
            "--runtime",
            str(tmp_path / "runtime"),
            "--output",
            str(tmp_path / "fixtures"),
        ]
    )

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error"
    assert output["reason"] == "make_fixtures_failed"
    assert "private details" not in json.dumps(output)
