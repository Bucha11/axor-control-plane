"""The example ids the screens hide are exactly the ones the demo seeds."""
from __future__ import annotations

from axor_backend import demo
from axor_backend.examples import is_example_node


def test_every_seeded_node_reads_as_an_example() -> None:
    for node in (demo.EX_BLOCK_NODE, demo.EX_PASS_NODE, demo.TREE_ORCH, demo.TREE_RESEARCH,
                 demo.TREE_WRITER, demo.TREE_SCRAPER, demo.CODING_CLAUDE, demo.CODING_CODEX,
                 demo.CODING_TREE, demo.SKILL_TRY, demo.OPS_AGENT, "claude-code-example-plan"):
        assert is_example_node(node), node


def test_a_real_session_is_not_one() -> None:
    for node in ("claude-code-vm-lena-s1-", "codex-laptop-1a2b3c4d", "support-bot",
                 "skill-try.claude-code.release-helper.k3j2"):
        assert not is_example_node(node), node
