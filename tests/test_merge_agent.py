from vibrant.agents.merge_agent import MergeAgent


def test_merge_agent_prompt_uses_centralized_template():
    prompt = MergeAgent.build_merge_prompt(
        task_id="task-123",
        task_title="Resolve merge conflicts",
        branch="vibrant/task-123",
        main_branch="main",
        conflicted_files=["src/app.py", "tests/test_app.py"],
        conflict_diff="<<<<<<< HEAD\nlocal\n=======\nmain\n>>>>>>> main",
        task_summary="Preserve both bug fixes and new tests.",
    )

    assert "You are a Merge Agent." in prompt
    assert "- **Task ID**: task-123" in prompt
    assert "- src/app.py" in prompt
    assert "- tests/test_app.py" in prompt
    assert "Preserve both bug fixes and new tests." in prompt
    assert "Do NOT introduce new features or make unrelated changes." in prompt
