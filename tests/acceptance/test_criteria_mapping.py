"""Formal acceptance suite mapped 1:1 to spec §15's 9 criteria (the project's definition of
done). Most of the underlying behavior is already exercised by focused tests built alongside
each engine module -- duplicating that coverage here would just be churn. This file is the
checklist: where each criterion's proof lives, plus the criteria that had no other natural home
and are implemented directly in this directory.

1. Golden path E2E, including the webhook-fired -> OpenMetadata-PATCH chain
   -- test_facade.py::test_golden_path_end_to_end (in-process, submit through accept)
   -- test_api.py::test_golden_path_over_http (over HTTP, repeated per plan)
   -- test_golden_path_webhook_and_openmetadata.py::
      test_accept_fires_webhook_which_drives_an_openmetadata_patch (the criterion's literal
      "on_proposal_accepted webhook fired -> OpenMetadata adapter PATCH called with provenance
      footer" clause, chained in one run -- a spec-compliance audit found this wasn't actually
      asserted end-to-end anywhere, only unit-tested in isolation; this closed that gap)

2. Dedup: asked exactly once, both consumers resolved
   -- test_facade.py::test_dedup_resolved_delivers_immediately_to_new_consumer
   -- test_facade.py::test_dedup_open_attaches_subscriber_without_a_second_ask

3. Escalation fires at T+escalation_after (clock injection), provenance.escalated=True
   -- test_facade.py::test_escalation_timer_reasks_configured_escalation_target
      (also pushes the escalation target's reply through and asserts
      `proposal.provenance.escalated is True` on the resulting proposal -- the audit found the
      request-row flag was tested but the proposal-level provenance flag wasn't)

4. Give-up fires on_request_failed(timeout) at T+give_up_after; no orphaned timers
   -- test_facade.py::test_give_up_timer_fails_request_and_notifies_consumer
   -- test_scheduler.py::test_cancel_timer_removes_apscheduler_job_and_marks_row_cancelled
      (a reply cancels pending timers rather than leaving them orphaned -- see capture.py)

5. Safety: mute persists; caps route around a saturated expert; quiet hours queue AND release;
   every outbound ask contains self-identification + opt-out
   -- test_capture.py::test_mute_sets_flag_and_audits
   -- test_capture.py::test_skip_reroutes_to_escalation_target
   -- test_asker.py::test_route_and_ask_routes_around_capped_expert
   -- test_asker.py::test_route_and_ask_queues_during_quiet_hours (the "queue" half)
   -- test_facade.py::test_quiet_hours_sweep_releases_a_queued_ask_once_the_window_passes (the
      "release" half -- previously only the interval trigger firing was tested, via a stub
      context, not a real queued request actually getting delivered once the window passed)
   -- test_asker.py::test_compose_message_contains_six_elements_in_order

6. Crash test: an in-flight timer survives a restart and fires correctly from the store
   -- test_scheduler.py::test_overdue_job_fires_on_a_fresh_scheduler_after_restart
      (real temp-file SQLite, not :memory:, per the plan)

7. Zero-LLM degradation: verbatim question, null structuring, no exceptions
   -- test_facade.py::test_zero_llm_degrades_to_verbatim_question_and_null_structured
   -- test_llm.py (LLMProvider-level call failure -> None/(None, None) degradation)

8. Fresh-machine setup: `pip install -e . && shtap init && shtap serve` reaches a demo
   -- test_fresh_machine.py::test_pip_install_init_and_serve_reach_a_working_demo
      (the one criterion with no other home; a real subprocess test, not an in-process one)
   -- test_wheel_install.py::test_wheel_install_ships_migrations_and_assets_and_serves
      (goes beyond the criterion's literal `-e .` wording to cover spec §2's actual promise,
      "`pip install shouldertap` ... = running": an *editable* install still sees the source
      tree, so it structurally cannot catch files that aren't in the wheel -- which is exactly
      how the Alembic migrations shipped broken until an audit caught it)

9. mypy --strict and ruff clean
   -- test_lint_and_types.py (runs over the whole `shouldertap/` package, not just the
      `engine/`+`sdk/` the spec's literal wording names -- a strict superset, so still passes
      the letter of the criterion, just also covers more)
"""
