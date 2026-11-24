"""Lanceur de l'agent côté serveur (agent/run_agent.py) : passages, plafonds de dépense, outils autorisés.
Un faux binaire « claude » remplace Claude Code : aucun appel réseau, aucune dépense."""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_agent", ROOT / "agent" / "run_agent.py")
run_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent)

FAKE_CLAUDE = """
import json, sys
args = sys.argv[1:]
with open(sys.argv[0] + ".calls", "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
                  "result": "Passage terminé", "total_cost_usd": COST}))
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    def make(cost: float) -> Path:
        script = tmp_path / "claude_fake.py"
        script.write_text(FAKE_CLAUDE.replace("COST", str(cost)), encoding="utf-8")
        monkeypatch.setenv("AGENT_CLAUDE_BIN", f"{Path(sys.executable).as_posix()} {script.as_posix()}")
        monkeypatch.setattr(run_agent, "mire_reachable", lambda: True)
        return Path(f"{script}.calls")
    return make


def calls(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_months_back_clamps_to_month_end():
    assert run_agent.months_back(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert run_agent.months_back(date(2026, 1, 15), 2) == date(2025, 11, 15)


def test_prompts_per_kind():
    today = date(2026, 11, 24)
    [(prompt, label)] = run_agent.prompts("decouverte", 6, today)
    assert "du 10 novembre 2026 au 24 novembre 2026" in prompt and "TYPE du passage : decouverte" in prompt
    assert "mardi 24 novembre 2026" in prompt
    slices = run_agent.prompts("rattrapage", 3, today)
    assert [lbl for _, lbl in slices] == [
        "du 24 octobre 2026 au 24 novembre 2026 (approfondi)", "du 24 septembre 2026 au 24 octobre 2026 (approfondi)",
        "du 24 août 2026 au 24 septembre 2026 (approfondi)"]
    [(offres, _)] = run_agent.prompts("offres", 6, today)
    assert "Welcome to the Jungle" in offres
    [(recherche, label)] = run_agent.prompts("recherche", 6, today)
    assert label == "recherche partant des offres" and '"kind": "recherche"' in recherche
    assert recherche.endswith("Date du jour : mardi 24 novembre 2026.")


def test_only_web_and_mire_api_tools():
    tools = run_agent.allowed_tools().split(",")
    assert "Bash" not in tools and all(not t.startswith("Bash(") or "agent/mire_api.py" in t for t in tools)
    assert "Edit(agent/tmp/**)" in tools


def test_budget_per_run_and_total(fake_claude, monkeypatch):
    calls_file = fake_claude(cost=1.8)
    monkeypatch.setenv("AGENT_MAX_BUDGET_USD", "2")
    monkeypatch.setenv("AGENT_TOTAL_BUDGET_USD", "3")
    monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-5-5")
    code = run_agent.main(["rattrapage", "--months", "3"])
    made = calls(calls_file)
    # 1re tranche : plafond 2 $ ; 2e : ce qui reste (1,20 $) ; 3e : annulée (plafond total atteint)
    assert [c[c.index("--max-budget-usd") + 1] for c in made] == ["2.00", "1.20"]
    assert all(c[c.index("--model") + 1] == "claude-sonnet-5-5" for c in made)
    assert all("--output-format" in c and c[c.index("--output-format") + 1] == "json" for c in made)
    assert code == 3


def test_single_run_succeeds(fake_claude, monkeypatch):
    calls_file = fake_claude(cost=0.4)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("AGENT_MAX_BUDGET_USD", raising=False)
    assert run_agent.main(["offres"]) == 0
    [args] = calls(calls_file)
    assert "--model" not in args and args[args.index("--max-budget-usd") + 1] == "5.00"


def test_unreachable_mire_stops_before_spending(monkeypatch):
    monkeypatch.setattr(run_agent, "mire_reachable", lambda: False)
    monkeypatch.setattr(run_agent, "run_claude", lambda *a: pytest.fail("aucun passage sans Mire"))
    assert run_agent.main(["decouverte"]) == 1
