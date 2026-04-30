## ADDED Requirements

### Requirement: Supported trigger modes
The action SHALL support exactly `bulk_commit` and `comment` as trigger modes.

#### Scenario: Removed automatic trigger aliases are rejected
- **WHEN** the action is configured with `trigger: auto` or `trigger: pull_request`
- **THEN** the action MUST fail with an unsupported trigger error

#### Scenario: Comment trigger remains command driven
- **WHEN** the action receives an `issue_comment` event with `trigger: comment`
- **THEN** the action MUST use the existing PR comment command authorization and parsing rules before starting a review

#### Scenario: Bulk commit ignores comment events
- **WHEN** the action receives an `issue_comment` event with `trigger: bulk_commit`
- **THEN** the action MUST skip without starting a review

### Requirement: Bulk commit opened review
The action SHALL review the full PR diff when `bulk_commit` handles a PR opened event.

#### Scenario: New PR is opened
- **WHEN** the action receives a `pull_request` event with action `opened` and `trigger: bulk_commit`
- **THEN** the action MUST review the full PR diff

#### Scenario: Opened review succeeds
- **WHEN** the full PR review for an opened event completes and comments are posted successfully
- **THEN** the action MUST persist the current PR head SHA as the last successfully reviewed SHA

### Requirement: Bulk commit synchronize review
The action SHALL review only the commit range from the last successfully reviewed SHA to the current PR head when `bulk_commit` handles a PR synchronize event.

#### Scenario: New commits are pushed after a successful previous review
- **WHEN** the action receives a `pull_request` event with action `synchronize`, `trigger: bulk_commit`, and a valid stored last reviewed SHA
- **THEN** the action MUST review the compare diff from the stored SHA to the current PR head SHA

#### Scenario: Synchronize review succeeds
- **WHEN** the synchronize review completes and comments are posted successfully
- **THEN** the action MUST update the stored last reviewed SHA to the current PR head SHA

#### Scenario: Stored SHA is missing
- **WHEN** the action receives a synchronize event and no stored last reviewed SHA exists for the PR
- **THEN** the action MUST review a safe full PR or base-to-head diff instead of skipping the review

#### Scenario: Stored SHA is invalid
- **WHEN** the stored last reviewed SHA is not usable for comparing to the current PR head
- **THEN** the action MUST review a safe full PR or base-to-head diff instead of skipping the review

### Requirement: Review state advancement
The action SHALL advance the per-PR last reviewed SHA only after the configured review policy completes successfully.

#### Scenario: Review generation fails
- **WHEN** review generation fails for a bulk commit review
- **THEN** the action MUST NOT update the stored last reviewed SHA

#### Scenario: Comment posting fails
- **WHEN** review generation succeeds but posting the aggregated review comments fails
- **THEN** the action MUST NOT update the stored last reviewed SHA

#### Scenario: State update fails after successful review
- **WHEN** review generation and comment posting succeed but persisting the last reviewed SHA fails
- **THEN** the action MUST report the state update failure

### Requirement: No short-window commit batching
The action SHALL NOT delay synchronize reviews to batch commits pushed within a short time window.

#### Scenario: Multiple synchronize events occur
- **WHEN** multiple synchronize events are delivered for separate pushes
- **THEN** each event MUST be eligible to start its own bulk commit review according to the stored last reviewed SHA

### Requirement: Multi-model configuration
The action SHALL allow a review to be configured with one or more models in a deterministic order.

#### Scenario: Multiple models are configured
- **WHEN** the action is configured with multiple models
- **THEN** the action MUST run every configured model in the configured order for the selected diff

#### Scenario: No multi-model list is configured
- **WHEN** no multi-model list is configured
- **THEN** the action MUST use the single configured model for the review

### Requirement: Multi-model aggregation
The action SHALL aggregate all configured model results into a single final review result before posting comments.

#### Scenario: All models complete successfully
- **WHEN** every configured model completes review for the selected diff
- **THEN** the action MUST deduplicate findings across model results and post one aggregated review result

#### Scenario: A configured model fails
- **WHEN** any configured model fails before aggregation completes
- **THEN** the action MUST fail the review and MUST NOT post a partial aggregated result

#### Scenario: Duplicate findings are produced
- **WHEN** multiple models produce findings for the same issue
- **THEN** the aggregated result MUST contain a single deduplicated finding for that issue
