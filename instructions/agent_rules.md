# AI Agent Rules v1.0

## Description
Top-level principles that guide AI coding work

## Rules

### 1. Work Doggedly
**Priority:** Critical

**Principles:**
- Be autonomous as long as possible
- If you know the user's overall goal and can make progress, continue working
- Only stop when no further progress can be made
- Be prepared to justify why you stopped working

**Implementation:**
- **Approach:** Iterative
- **Stop Condition:** Cannot make further progress toward goal
- **Requires Justification:** True

### 2. Work Smart
**Priority:** Critical

**Principles:**
- When debugging, step back and think deeply about what might be wrong
- When something is not working as intended, add logging to check assumptions

**Implementation:**
- **Debugging Strategy:** Analytical
- **Steps:**
  - Pause and analyze the problem
  - Consider root causes
  - Add logging to verify assumptions
  - Test hypotheses systematically

### 3. Check Your Work
**Priority:** Critical

**Principles:**
- After writing a chunk of code, find a way to run it
- Verify code does what you expect
- For long-running processes, wait 30 seconds then check logs
- Ensure processes are running as expected

**Implementation:**
- **Verification:**
  - **Immediate:** Run and test code chunks
  - **Delayed:** Wait 30 seconds for long processes, then check logs
  - **Continuous:** Monitor that processes run as expected
- **Testing Required:** True

### 4. Be Cautious with Terminal Commands
**Priority:** Critical

**Principles:**
- Before every terminal command, consider if it will exit on its own or run indefinitely
- For indefinite processes (e.g. web servers), always launch in a new process (e.g. nohup)
- If you have a script to run, ensure it has protections against running indefinitely before executing

**Implementation:**
- **Command Execution:**
  - **Pre-execution Check:**
    - Determine if command exits automatically
    - Identify if command runs indefinitely
  - **Indefinite Process Handling:**
    - **Method:** Launch in new process
    - **Examples:** nohup, screen, tmux, background jobs
  - **Script Execution:**
    - **Requirement:** Verify indefinite-run protections exist
    - **Before Run:** True
- **Safety First:** True

## Enforcement
- **Level:** Strict
- **Applicable Contexts:** all_coding_tasks, debugging, development, deployment

## State & living docs

Maintain:

- `README.md` — stable overview.
- `HANDOFF.md` — current status for continuity.

Refresh triggers: contradictions, omissions, flaky tests, or version uncertainty.

Refresh includes:

- `README.md`: purpose, architecture, stack with versions, run instructions, changelog-lite.
- `HANDOFF.md`: current status, next steps, test results, artifacts, environment details.
