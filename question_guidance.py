"""Evidence-backed recovery guidance, independent of the model provider.

Suggestions are questions, never substitute answers. The gateway verifies each
candidate; its evidence is not injected into the conversation's observed ledger.
"""
from __future__ import annotations

from datetime import date
from itertools import combinations
import re

import agent_tools

STOP = set("what which how did does is are the a an in on at for of and or to me show tell please between with its source sources report include".split())
TOPICS = {
    "vacancy": r"vacan|availability",
    "leasing": r"take.up|leasing|pre.?let",
    "rent": r"rent|incentive",
    "pipeline": r"pipeline|construction|complet|deliver|development",
    "macro": r"gdp|employment|unemployment|bank rate|econom|interest rate",
    "esg": r"epc|energy|certificate|breeam|sustainab",
    "demand": r"hybrid|quality|home.work|working from home",
}


def unsupported_intent(text):
    """Conservative capability checks; factual reported forecasts remain valid."""
    if re.search(r"\b(?:real[ -]?time|right now|today(?:'s)?)\b",text,re.I) and not re.search(r"\b(?:fixture|historical|recorded)\b",text,re.I):
        return "live_data", "This fixture is a historical evidence sample and has no live market-data or web-ingestion capability."
    if re.search(r"\b(?:predict|extrapolate)\b|^(?:please\s+)?forecast\b",text,re.I) and not re.search(r"\b(?:reported|published|source|fact)\b|\bF[0-9]{2,}\b",text,re.I):
        return "unsupported_prediction", "The fixture can report dated observations and published forecasts, but it cannot substantiate a new prediction."
    if re.search(r"\bshould I (?:buy|sell|invest)|\brecommend (?:an? )?investment\b",text,re.I):
        return "investment_advice", "The curated observations do not establish a personalized investment recommendation."
    if re.search(r"\b(?:delete|write|execute|run)\s+(?:the\s+)?(?:sql|files?|database)|\b(?:call|invoke)\s+(?:the\s+)?(?:external\s+)?api\b|\bsend (?:an? )?email\b|\bbrowse the web\b",text,re.I):
        return "unsupported_action", "This demo provides five read-only evidence tools; it cannot modify files or databases, send messages, browse the web or call an external API."
    return None


def tokens(text):
    return set(re.findall(r"[\w]+", text.casefold().replace("_", " "))) - STOP


def period_label(fact):
    start, end = date.fromisoformat(fact["period_start"]), date.fromisoformat(fact["period_end"])
    kind = fact["period_kind"]
    if kind in ("quarter", "quarter_end"):
        return f"Q{(end.month-1)//3+1} {end.year}"
    if kind in ("half_year", "half_year_end"):
        return f"H{1 if end.month<=6 else 2} {end.year}"
    if kind in ("year", "annual", "calendar_year"):
        return str(end.year)
    return fact["period_start"] if start == end else f"{start.isoformat()} to {end.isoformat()}"


def _candidates(facts):
    result = []
    for fact in facts:
        subject = fact["entity"] if fact["geography_basis"] in ("individual_property", "assessed_premises") else fact["geography"]
        question = f"What does {fact['fact_id']} report about {subject} {fact['metric'].replace('_',' ')} at {period_label(fact)}? Include its source."
        result.append({"question":question, "tool":"get_fact", "arguments":{"fact_id":fact["fact_id"]},
                       "fact_ids":[fact["fact_id"]], "facts":[fact], "kind":"lookup"})
    for a,b in combinations(facts,2):
        time_series = a["series_key"] and a["series_key"] == b["series_key"]
        cross_section = (a["metric"] == b["metric"] and a["unit"] == b["unit"]
                         and a["geography"] != b["geography"] and a["period_start"] == b["period_start"]
                         and a["period_end"] == b["period_end"])
        if not (time_series or cross_section): continue
        args = {"fact_id_a":a["fact_id"],"fact_id_b":b["fact_id"],"mode":"time_series" if time_series else "cross_section"}
        checked = agent_tools.invoke("compare_facts",args)
        if not checked["ok"]: continue
        if time_series:
            first,last = sorted((a,b),key=lambda f:f["period_start"])
            question = f"How did {a['geography']} {a['metric'].replace('_',' ')} change between {period_label(first)} and {period_label(last)} ({first['fact_id']} and {last['fact_id']})?"
        else:
            question = f"Compare {a['metric'].replace('_',' ')} in {a['geography']} and {b['geography']} for {period_label(a)} using {a['fact_id']} and {b['fact_id']}."
        result.append({"question":question,"tool":"compare_facts","arguments":args,
                       "fact_ids":[a["fact_id"],b["fact_id"]],"facts":[a,b],"kind":"compare"})
    # A direction question spanning two places needs two validated series,
    # rather than a suggestion that silently drops one requested geography.
    comparisons=[c for c in result if c["kind"]=="compare" and c["arguments"]["mode"]=="time_series"]
    for left,right in combinations(comparisons,2):
        a,b=left["facts"],right["facts"]
        if a[0]["geography"]==b[0]["geography"] or a[0]["metric"]!=b[0]["metric"] or a[0]["unit"]!=b[0]["unit"]:continue
        if sorted((f["period_start"],f["period_end"]) for f in a)!=sorted((f["period_start"],f["period_end"]) for f in b):continue
        first,last=sorted(a,key=lambda f:f["period_start"])
        ids=left["fact_ids"]+right["fact_ids"]
        question=f"Did {a[0]['geography']} and {b[0]['geography']} {a[0]['metric'].replace('_',' ')} move in the same direction between {period_label(first)} and {period_label(last)} ({', '.join(ids)})?"
        result.append({"question":question,"tool":"compare_facts","arguments":left["arguments"],
                       "fact_ids":ids,"facts":a+b,"kind":"compare",
                       "support_calls":[{"tool":"compare_facts","arguments":left["arguments"]},
                                        {"tool":"compare_facts","arguments":right["arguments"]}]})
    # Source-only records can answer access questions even when they have no facts.
    catalogue=agent_tools.invoke("coverage_gap",{"request":"Source access catalogue"}).get("data") or {}
    for source_id in sorted({f["source_id"] for f in facts} | {s for row in catalogue.get("coverage_rows",[]) for s in row["sources"]}):
        checked = agent_tools.invoke("get_source",{"source_id":source_id})
        if checked["ok"]:
            source=checked["data"]
            result.append({"question":f"Show source {source_id} and its access qualifications.","tool":"get_source",
                           "arguments":{"source_id":source_id},"fact_ids":[],"facts":[],"kind":"source",
                           "source_text":" ".join(str(source.get(k,"")) for k in ("title","notes","access_status"))})
    return result


def nearest_supported_question(user_text, state):
    catalog=agent_tools.invoke("search_facts",{"limit":100})
    if not catalog["ok"] or catalog["data"]["truncated"] or not catalog["data"]["facts"]:
        return None
    facts=catalog["data"]["facts"]
    requested=tokens(user_text[:4000])
    topic_matches={topic for topic,pattern in TOPICS.items() if re.search(pattern,user_text,re.I)}
    compare=bool(re.search(r"chang|compar|differ|direction|versus|\bvs\b",user_text,re.I))
    access=bool(re.search(r"source|access|login|api|token|provenance",user_text,re.I))
    explicit_ids=set(re.findall(r"\b[FS][0-9]{2,}\b",user_text,re.I))
    places={f["geography"] for f in facts if re.search(r"(?<!\w)"+re.escape(f["geography"])+r"(?!\w)",user_text,re.I)}
    years=[int(y) for y in re.findall(r"\b(?:19|20)[0-9]{2}\b",user_text)]
    def score(candidate):
        fs=candidate["facts"]
        words=tokens(candidate["question"]+" "+candidate.get("source_text",""))
        value=2*len(requested & words)
        value+=20*len(topic_matches & {f["topic"] for f in fs})
        value+=10*len(places & {f["geography"] for f in fs})
        if places:value-=4*len({f["geography"] for f in fs}-places)
        value+=18*len(explicit_ids & (set(candidate["fact_ids"]) | {candidate["arguments"].get("source_id","")}))
        value+=8*(compare and candidate["kind"]=="compare")
        value+=10*(access and candidate["kind"]=="source")
        value+=2*len(set(state.get("topics",[])) & {f["topic"] for f in fs})
        value+=2*len(set(state.get("geographies",[])) & {f["geography"] for f in fs})
        if years and fs:value-=min(abs(int(f["period_end"][:4])-year) for f in fs for year in years)
        return value
    choices=_candidates(facts)
    chosen=sorted(choices,key=lambda c:(-score(c),c["question"]))[0]
    calls=chosen.get("support_calls",[{"tool":chosen["tool"],"arguments":chosen["arguments"]}])
    if not all(agent_tools.invoke(call["tool"],call["arguments"])["ok"] for call in calls):return None
    return {**{key:chosen[key] for key in ("question","tool","arguments","fact_ids")},"support_calls":calls}


REASONS = {
    "referent":"The request does not identify one geography or asset, so choosing a referent would be a guess.",
    "period":"The observation dates are missing or ambiguous; earlier dates cannot safely replace a new date request.",
    "period_granularity":"The available quarterly office observations do not establish the requested monthly change.",
    "comparison_baseline":"Only one observation period is selected. Comparing directions of change requires a baseline and a later period for each named geography; the previous gap does not establish a trend.",
    "metric":"The measure is unspecified or incompatible with the requested comparison.",
    "geography":"The publisher geography is unspecified or ambiguous; the fixture does not establish an equivalent area.",
    "entity":"The building or entity is not identified clearly enough to retrieve its evidence.",
    "scope":"The metric, geography, entity or observation period is not sufficiently resolved to support an answer.",
}


def make_recovery(user_text,state,status,claims,observations,clarification=None,error_code=None):
    gaps=[observations[c["ref"]] for c in claims if c["kind"]=="coverage" and c["ref"] in observations
          and observations[c["ref"]]["result"]["ok"] and observations[c["ref"]]["result"]["data"]["assessment"]=="no_matching_facts_in_fixture"]
    if status=="answered" and not gaps:return None
    if status=="clarified" and clarification=="getting_started":return None
    if status=="clarified":code,reason=clarification or "scope",REASONS.get(clarification,REASONS["scope"])
    elif gaps:code,reason="missing_evidence","The fixture lacks observations for part or all of the requested scope. A missing observation cannot establish a market value or a change."
    elif status=="refused":code,reason="unsupported_request","This read-only demo can substantiate questions from its curated London office evidence; it cannot carry out or substantiate the requested task."
    elif status=="provider_error":code,reason="provider_error",f"The selected provider failed ({error_code}); this is an execution failure, not evidence that the market observation is missing."
    elif status=="budget_exhausted":code,reason="step_limit","The request did not resolve within the bounded tool-step budget. A narrower evidence lookup can be verified directly."
    elif status=="conversation_limit":code,reason="conversation_limit","This conversation has reached its evidence-observation limit. Use /new before asking the suggested question."
    elif status=="configuration_error":code,reason="configuration_error","The provider or local configuration is unavailable or invalid. Correct the reported setup problem before trying the suggested evidence question."
    else:code,reason="invalid_input","The input must be a nonblank text request of at most 4,000 characters; the current input cannot be processed as a turn."
    capability=unsupported_intent(user_text) if isinstance(user_text,str) else None
    if status=="refused" and capability:code,reason=capability
    failures={o["result"]["error"]["code"] for o in observations.values() if not o["result"]["ok"]}
    if not gaps and status not in ("provider_error","budget_exhausted"):
        if "NOT_FOUND" in failures:code,reason="unknown_id","The requested fact or source identifier is not present in this fixture."
        elif "COMPARISON_REFUSED" in failures:code,reason="incompatible_comparison","The requested records do not meet the gateway's compatibility rules, so subtracting them would be misleading."
    suggestion=nearest_supported_question(user_text if isinstance(user_text,str) else "",state)
    return {"reason_code":code,"reason":reason,"suggestion":suggestion}


def with_recovery(base_text,recovery):
    if recovery is None:return base_text
    suffix="Reason: "+recovery["reason"]
    if recovery["suggestion"]:
        suffix+="\nSuggested supported question: "+recovery["suggestion"]["question"]
    else:
        suffix+="\nA supported alternative cannot be verified while the evidence gateway is unavailable."
    return base_text+"\n\n"+suffix
