# Demo script (about 10 minutes)

Everything is synthetic and dry-run. Use the user switcher in the top bar; the password for every demo user is `demo`.

1. **Overview** (operator). The seed already ran *Market-to-ICP*: market research, personas, competitor gaps and a
   draft ICP v1 that is **waiting for approval**. The guided checklist tracks progress.
2. **Approvals**. As operator you cannot decide (role). Switch to **reviewer** and approve *ICP activate*. (Try the
   admin starting a manual ICP edit and approving it: the four-eyes rule refuses.)
3. **ICP editor**. Criteria weights, tier thresholds, hard disqualifiers (separate from weights), model version and the
   research evidence. *Edit as new draft* -> change weights -> *Submit for approval* creates a new version.
4. **Source adapters** (operator). *Run account discovery* (sample CSV + mock registry): 26 input records become 22
   accounts after dedup. *Run contact discovery*: CSV + mock licensed dataset, role matching, verification,
   qualification and suppression checks.
5. **Accounts & scoring**. Open *Brightlane Logistics*: three sources merged, criteria contributions with evidence,
   missing data, model version, score history. Note the disqualified accounts (gambling, competitor, US, suppressed
   domain, too small).
6. **Contacts**. Distinct verification levels: syntax invalid, syntax valid (no MX), domain has MX, provider verified
   (MOCK). Nobody is "deliverable" before a (simulated) delivery event. Olivia Grant is suppressed (seeded opt-out).
7. **Campaigns**. *Create & draft*: segmentation, evidence-cited drafts (generic drafts are labelled), policy checks.
   The workflow stops at the approval gate; the queue is empty.
8. **Approvals** (reviewer). Optionally exclude a draft on the Campaigns page first, then approve *campaign activate*.
   Step-1 messages are queued (suppression re-checked).
9. **Campaigns** (operator). *Run send + inbox cycle*: dry-run dispatch, simulated delivery/reply/bounce events,
   classification, suppression of the unsubscribe and the bounce, follow-up cancellation, response drafts, mock CRM.
10. **Reply triage**. Pricing and legal questions are escalated, the product question gets a draft citing an approved
    passage, the ambiguous reply goes to a human (low confidence).
11. **Campaigns**: *Advance simulated clock +3 days* and run the cycle again: follow-ups go only to non-responders.
12. **Analytics**. Contact-level rates with numerators/denominators/formulas, segment and source breakdowns.
    *Generate ICP recommendations* proposes ICP v2, which the reviewer must approve.
13. **Governance** (admin). Emergency **STOP** halts everything; suppression list; mock CRM outbox; *Reset demo*.
14. **Workflows / Agents / Audit**. Every run, step, retry, error, agent action (with evidence ids), approval and
    send is traceable.

The same flow runs headless in `backend/tests/test_e2e_api.py`; the UI walkthrough was driven with Playwright to
produce `docs/screenshots/`.
