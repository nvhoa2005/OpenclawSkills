---
name: meta-ads-pipeline
description: Self-contained Meta Ads pipeline skill that parses raw user text (IDs/links), runs bundled DogBot per input in a loop, and merges successful outputs into one final Excel and one final crawl JSON. Use when users in any session send mixed links/IDs and expect end-to-end execution without manual normalization.
---

# Meta Ads Pipeline

Run full pipeline directly from this skill package. This skill is designed for multi-user safety, ensuring that concurrent runs are fully isolated and do not interfere with each other.

## Execution Policy (required)

This script should be executed directly using the `exec` tool.

-   **Workflow:** The agent must call the `exec` tool and wait for the JSON output. If successful, the agent can then immediately use the file paths from the output in a subsequent tool call (e.g., `message`) within the same turn.

## Command

The public command remains simple. The script internally handles all pathing and isolation.

**Important:** 
    - The command must be executed from the `~/.openclaw/workspace-meta-ads` directory.
    - You must pass the raw user text, channel_name and recipient_id correctly.

```bash
python3 skills/meta-ads-pipeline/scripts/run_meta_ads_pipeline.py --input "<raw user text>" --send-channel "<channel_name>" --send-target "<recipient_id>"
```

## Architecture & Execution Flow

This skill uses a fingerprint-based isolation model to support concurrent runs and robust checkpointing.

1.  **Fingerprinting:**
    -   Extracts all URLs and numeric IDs from the raw input text.
    -   The extracted, deduplicated, and sorted list of inputs is used to generate a unique SHA256 **fingerprint** for the specific job request. This ensures that identical requests always have the same fingerprint, regardless of the user's phrasing.

2.  **Isolated Workspace:**
    -   A unique directory is created for the entire job: `<openclaw-root>/workspace-meta-ads/dogbot/dogbot_runs/<fingerprint>/`.
    -   All operations for this run (video downloads, temporary outputs, state files) are contained entirely within this directory, preventing any cross-contamination between concurrent runs.

3.  **Execution & Checkpointing:**
    -   The pipeline bootstraps dependencies (pip install) if needed.
    -   It then checks for a state file within its isolated workspace (`.../<fingerprint>/outputs/meta_ads_pipeline_state_<fingerprint>.json`).
    -   **If a state file exists**, it resumes from the last completed step. A user can resume a failed or interrupted job simply by sending the exact same request again.
    -   The pipeline loops through the remaining inputs and executes the bundled `scripts/dogbot_pipeline.py` for each.
    -   `dogbot_pipeline.py` also has its own per-video checkpointing, making the process highly resilient.

4.  **Artifact Merging & Finalization:**
    -   Upon successful completion of all inputs, the pipeline merges all generated artifacts into a single final Excel file and a single final JSON file within the isolated workspace.

5.  **File Handling & Cleanup:**
    -   The two final artifacts (`merged_...xlsx`, `merged_...json`) are **moved** to the persistent storage directory: `<openclaw-root>/workspace-meta-ads/dogbot/dogbot_final_outputs/`.
    -   The final Excel file is **copied** to a staging area for delivery: `<openclaw-root>/workspace-meta-ads/file_send_meta/`.
    -   The entire isolated run directory (`.../dogbot/dogbot_runs/<fingerprint>/`) is **deleted** to clean up all temporary files.

## Output Paths

-   **Transient Run Directory (Auto-cleaned):** `<openclaw-root>/workspace-meta-ads/dogbot/dogbot_runs/<fingerprint>/`
-   **Final Artifacts (Persistent):** `<openclaw-root>/workspace-meta-ads/dogbot/dogbot_final_outputs/`
-   **Staging for Sending:** `<openclaw-root>/workspace-meta-ads/file_send_meta/`

## Output Contract

The final stdout JSON from the script includes:
-   `status`: `success | partial | failed`
-   `summary`: A summary of the run.
-   `artifacts.excel_path`: The final, persistent path to the merged Excel file.
-   `artifacts.crawl_json_path`: The final, persistent path to the merged JSON file.
-   `artifacts.staged_for_send_path`: The path to the Excel file copied to the staging area, ready to be sent.
-   `runs`: Detailed results for each individual input.
-   `checkpoint`: Information about the fingerprint and any reused runs.
-   `paths`: Key directories used during the run.
