# Investigation: GPT-4 Generic Non-Answer Issue (#93)

## Problem Statement

When asked sophisticated questions like "Does HED have a CLI?", GPT-4 (gpt-oss-120b via Cerebras) was returning generic non-answers:

> "I'm ready to help with any HED-related question you have. Please let me know what you'd like assistance with..."

Meanwhile, Qwen (qwen3-235b-a22b-2507) provided detailed, accurate responses.

## Hypothesis: Overly Literal Tool Usage Directive

### Root Cause

The HED system prompt contains this directive (line 65 of config.yaml):

```
Before responding, use the retrieve_hed_docs tool to get any documentation you need.
```

**GPT-4 Interpretation:** "I MUST use retrieve_hed_docs tool BEFORE I can respond"
- Too literal/conservative
- Gets stuck when unsure which docs to retrieve
- Defaults to safe generic response

**Qwen Interpretation:** "Use retrieve_hed_docs when helpful"
- More flexible
- Answers directly when it has knowledge
- Only retrieves docs when actually needed

### Supporting Evidence

1. **Prompt Wording is Very Strong**
   - "Before responding" is a hard temporal constraint
   - GPT-4 is known to be more conservative/risk-averse
   - Combined with low temperature (0.1), encourages safe responses

2. **Question Was Straightforward**
   - "Does HED have a CLI?" is not ambiguous
   - Should be answerable from preloaded docs or general knowledge
   - No doc retrieval actually needed

3. **Qwen Answered Without Tool Use**
   - Qwen provided factual answer directly
   - Listed available HED tools (Python, MATLAB, JavaScript)
   - Correctly stated no official CLI exists
   - Did not retrieve additional docs

4. **Ambiguity Handling Directive**
   - Line 38-39: "When a user's question is ambiguous... ask clarifying questions when necessary"
   - GPT-4 might perceive "CLI" as ambiguous (command-line tools vs. specific CLI program)
   - Defaults to "I'm ready to help" rather than making assumption

## Model Behavior Differences

| Aspect | GPT-4 (gpt-oss-120b) | Qwen (qwen3-235b-a22b-2507) |
|--------|----------------------|------------------------------|
| **Prompt Interpretation** | Literal, conservative | Flexible, pragmatic |
| **Tool Usage** | "Must use before responding" | "Use when helpful" |
| **Risk Tolerance** | Low - prefers safe responses | Higher - answers directly |
| **Ambiguity Handling** | Asks for clarification | Makes reasonable assumptions |
| **Temperature Sensitivity** | More affected by low temp (0.1) | Less conservative at low temp |

## Additional Contributing Factors

### 1. **Temperature Setting**
- Current: 0.1 (very low)
- Low temperature encourages deterministic, safe responses
- GPT-4 may become overly cautious

### 2. **System Prompt Length**
- HED prompt is ~200 lines (very detailed)
- Includes many directives and guidelines
- GPT-4 might get confused by conflicting signals:
  - "Use tools liberally" (line 54)
  - "Before responding, use retrieve_hed_docs" (line 65)
  - "Assume most likely meaning and provide useful starting point" (line 38)
  - "Ask clarifying questions when necessary" (line 39)

### 3. **Provider-Specific Behavior**
- GPT-4 via Cerebras might have different behavior than native OpenAI
- Routing through OpenRouter + specific provider could introduce quirks

## Recommended Fixes

### Fix 1: Soften Tool Usage Directive (Recommended)

**Current (line 65):**
```
Before responding, use the retrieve_hed_docs tool to get any documentation you need.
```

**Proposed:**
```
When you need additional information, use the retrieve_hed_docs tool to get relevant documentation.
For straightforward factual questions that you can answer from preloaded docs or general HED knowledge, you may respond directly.
```

### Fix 2: Increase Temperature

**Current:** 0.1
**Proposed:** 0.3

Rationale: Allow more flexibility in responses while maintaining accuracy.

### Fix 3: Simplify System Prompt

- Reduce conflicting directives
- Prioritize "answer directly when possible" over "always use tools"
- Move detailed guidelines to later in prompt

### Fix 4: Model-Specific Prompts

Create model-specific prompt variants:
- GPT-4: More explicit about when to answer directly
- Qwen: Current prompt works well

## Testing Plan

1. **Test with Original Prompt + Higher Temperature**
   - Set temperature to 0.3
   - Test "Does HED have a CLI?" question
   - See if GPT-4 still gives generic response

2. **Test with Softened Directive**
   - Update line 65 as proposed
   - Keep temperature at 0.1
   - Test same question

3. **Test with Both Changes**
   - Apply both fixes
   - Test multiple sophisticated questions
   - Compare quality with Qwen responses

4. **Regression Testing**
   - Ensure tool retrieval still works when needed
   - Test validation and tag suggestion features
   - Verify no degradation in annotation quality

## Conclusion

The most likely cause is GPT-4 interpreting "Before responding, use the retrieve_hed_docs tool" too literally, combined with:
- Low temperature (0.1) encouraging conservative behavior
- Long, detailed system prompt with many directives
- GPT-4's inherent risk-averse nature

**Immediate action taken:** Switched to Qwen (PR #94, merged to main)

**Next steps:** Test hypothesis by softening tool usage directive and/or increasing temperature for GPT-4.

## Related Issues

- Issue #93: GPT-OSS stuck in a common response
- PR #94: Switch to Qwen with DeepInfra/FP8 provider (merged)
