# Extraction baseline — first real ingest

Measured 9 September 2026 against Chris's own exports: a Claude archive (258
conversations) and a ChatGPT archive (4,259 conversations, 17,881 user turns). This
is the number to beat, recorded because "extraction got better" is not a claim
anyone should have to take on trust.

## What the first run showed

312 objects. Every one of them stamped `account_export_parse` at confidence 0.60 —
whether Claude had already curated it or a regex had lifted it out of prose.

| | ChatGPT | Claude |
|---|---|---|
| Memories | 117 | 191 |
| Median length | 53 chars | 125 chars |
| Under 45 chars | 42% | 8% |
| Starts lowercase (raw turn text) | 26% | 19% |
| Transient phrasing | 18% | 1% |

The mined half is not shippable. `I run the build site`, `i run into infinite
recursion`, `I use retrieval augmented generation to train gpt-4` are not durable
preferences; they are conversational fragments matched by the `\bi (?:use|run)\b`
pattern, whose own comment already concedes it is "weaker than *I prefer*".

Recall is just as bad, and the Claude side isolates it exactly: of 195 objects,
**188 came from Claude's curated `memories/` files and only 7 from mining 1,229
conversation turns.** ChatGPT's regex path recovered 117 memories from 17,881 turns
— 0.65%.

So the value was never in the mining. It was in importing extraction that a model
had already done, with the whole conversation in front of it.

## Fix 1: the graph now records which half a memory came from

`PROVIDER_CURATED` at 0.85 for what a provider already extracted — `memories/`,
project instructions, uploaded docs. `ACCOUNT_EXPORT_PARSE` stays at 0.60 for mined
prose. After re-import:

```
by method:     {'provider_curated': 188, 'account_export_parse': 124}
by confidence: {'0.85': 188, '0.60': 124}

curated: n=184  median 126 chars  under45 7%   transient 0%
mined:   n=124  median  53 chars  under45 42%  transient 18%
```

Two clean populations, and the Context Inspector can now say which one it is
looking at — which is the thing a product about provenance cannot be missing.

Ranking cares too. `"what do I do for work"` before and after:

| Before (all 0.60) | After |
|---|---|
| **I work at the gym as well** | description: How Christopher prefers to work |
| I use the subway to get to work | Recent work-shaped activity… |
| description: How Christopher prefers to work | Thinks well during deep, uninterrupted flow work |
| … | Found client-facing diagnostic work… energizing |
| Christopher is a Data Scientist at JPMorgan… (#10) | I work at the gym as well (#5) |

Nothing about retrieval changed. Flat confidence had simply given the ranker
nothing to prefer with.

## Fix 2: model-assisted extraction — built, blocked on credits

The path exists and is schema-constrained: `responses.parse` with
`text_format=Proposal` means the model fills the schema directly, so it has nowhere
to return a confidence, a locality, or an object id. `store=False` on every call.

Two defects were fixed before it could be used at all:

- **The API key never reached the client.** pydantic loads `.env` into settings
  without exporting to `os.environ`, so a key sitting in the project's own `.env`
  was invisible to the SDK and every call failed with "Missing credentials".
- **A permanent failure was treated as a per-turn one.** An unfunded account raises
  429, which was classified as `ExtractionUnavailable` — the code the importers
  catch and continue on. Over this archive that is **17,881 doomed API calls**
  reported as a tidy `unavailable` count instead of a stop. Quota and auth failures
  now raise `ExtractionConfigurationError`, which the importers do not catch.

The Claude importer also had no model branch at all; only ChatGPT did. It has one
now, behind the same `extraction_mode=model` opt-in.

**It cannot run yet.** The account returns:

```
"code": "credit_balance_exhausted"
"message": "You have no credits remaining."
```

Local models are not a substitute on this machine and were not pursued: llama3.1 is
4.7 GB against 8 GB of RAM and times out, and a 0.5b model does not clear the
false-positive bar.

## Results: regex vs. model, on the same 1,229 Claude turns

Every column is the same archive through the same importer. Only extraction differs.

| | regex | model, first prompt | model, current prompt |
|---|---|---|---|
| Objects | 195 | 571 | 411 |
| Mined memories | **7** | 171 | **84** |
| Entities | 0 | 171 | 105 |
| Facts | 0 | 41 | 34 |
| Median length | 49 ch | 68 ch | **84 ch** |
| Under 45 chars | 43% | 29% | **19%** |
| Transient phrasing | 14% | **0%** | **0%** |
| Per-task imperatives | 0% | 4% | 5% |

**Recall: 7 → 84, a 12x increase**, and the regex column's 7 is the honest ceiling
of pattern matching over years of prose it was never tuned for. Entities and facts
go from nothing to a populated graph; patterns can only emit first-person memories,
so a third party had nowhere to go except the user's own profile.

The current prompt extracts *half* what the first one did (84 against 171) and that
is the point. Precision-over-recall is the standing rule, the quality metrics all
move the right way, and the drop is the prompt declining turns the earlier one
accepted.

### What is still wrong

**~5% of mined memories are per-task imperatives**: `Dont import any outside
modules`, `dont use munchin and crunchin`, `make the responses simpler`. These are
commands to an assistant inside one piece of work, not durable facts about the user.
The prompt's TRANSIENT examples cover narration and requests but not instructions,
which read as standing preferences because grammatically that is what they look
like. Regex scored 0% here only because its patterns could not match an imperative
at all.

**Third-person statements still land as first-person memories**: `He has prior
experience in software devolpment` is a claim about someone else, and belongs in
`facts` linked to an entity. The rule exists in the prompt for pasted text; it does
not yet cover the user describing a third party in their own words.

Both are prompt work, measurable against this table.

## Running it once there are credits

```sh
export COLETAR_EXTRACTION_MODE=model
export COLETAR_EXTRACTION_PROVIDER=openai   # opt-in; the shipped default stays ollama
coletar import-claude  <export-folder> --tenant tenant_<new>
coletar import-chatgpt <archive.zip>   --tenant tenant_<new>
```

Import into a **fresh tenant** and compare against the table above rather than
re-importing over this graph, so the two runs stay separable.

Budget before starting: 17,881 turns is 17,881 calls. Sample a few hundred first and
multiply — the fail-fast fix means a billing problem stops the run immediately, but
it will not stop one that is merely expensive.
