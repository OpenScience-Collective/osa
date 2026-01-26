# Proposed Fixes for GPT-4 Non-Answer Issue

## Summary

Based on investigation documented in `gpt4_non_answer_investigation.md`, we've identified that GPT-4's overly literal interpretation of "Before responding, use the retrieve_hed_docs tool" causes generic non-answers for straightforward questions.

## Changes Made

### 1. Softened Tool Usage Directive (config.yaml lines 63-66)

**Before:**
```yaml
Before responding, use the retrieve_hed_docs tool to get any documentation you need.
Include links to relevant documents in your response.
```

**After:**
```yaml
You can answer straightforward factual questions directly using your preloaded documentation and general HED knowledge.
When you need additional information beyond what's preloaded, use the retrieve_hed_docs tool to get relevant documentation.
Include links to relevant documents in your response.
```

**Rationale:**
- Removes hard temporal constraint ("Before responding")
- Explicitly permits direct answers for straightforward questions
- Still encourages doc retrieval when needed
- Should work better with conservative models like GPT-4

## Additional Considerations

### Temperature Adjustment (Optional)

Current temperature: 0.1 (very low)
Proposed: 0.2-0.3 for GPT-4

**Pros:**
- More flexible, less conservative responses
- Better balance between accuracy and helpfulness

**Cons:**
- May reduce consistency
- Not needed if prompt change fixes issue

**Recommendation:** Test prompt change first. If still seeing generic responses, try temperature 0.3.

### Model-Specific Configuration (Future)

Consider adding per-community temperature overrides:

```yaml
# Optional temperature override for this community
# llm_temperature: 0.3
```

This would allow HED to use different temperature than platform default.

## Testing Plan

### Phase 1: Validate Prompt Change with Qwen
1. Deploy updated config to dev
2. Test with Qwen (current default)
3. Ensure no regression in behavior
4. Verify doc retrieval still works when needed

### Phase 2: Test with GPT-4 (If/When Re-Enabled)
1. Temporarily switch HED config to gpt-oss-120b
2. Test with questions that previously failed:
   - "Does HED have a CLI?"
   - "What's the latest version of HED schema?"
   - "How do I validate HED strings in Python?"
3. Verify direct answers provided
4. Ensure tool usage when needed

### Phase 3: Cost/Quality Comparison
1. Run benchmark question set
2. Compare GPT-4 vs Qwen:
   - Answer quality
   - Cost per query
   - Tool usage frequency
   - Response time
3. Decide whether to re-enable GPT-4 or keep Qwen as default

## Success Criteria

1. **Direct Answers:** Model answers straightforward questions without generic "I'm ready to help" responses
2. **Tool Usage:** Model still retrieves docs when information is not in preloaded context
3. **Accuracy:** Answers remain factually correct
4. **No Regressions:** Validation, tag suggestion, and other tools continue working

## Risk Assessment

**Low Risk:**
- Prompt change is minimal and logical
- Only affects tool usage timing, not overall behavior
- Qwen already works with current prompt, so change should be safe
- Can easily revert if issues arise

## Timeline

1. **Immediate:** Deploy prompt change to dev (with current Qwen default)
2. **Week 1:** Monitor dev usage, collect feedback
3. **Week 2:** If stable, promote to prod
4. **Future:** Re-test GPT-4 with updated prompt if/when reconsidering model choice

## Related Files

- Investigation: `.context/gpt4_non_answer_investigation.md`
- Config change: `src/assistants/hed/config.yaml` lines 63-72
- Issue: #93
- PR: #94 (model switch to Qwen)
