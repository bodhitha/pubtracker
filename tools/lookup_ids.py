#!/usr/bin/env python3
"""Suggest INSPIRE IDs and ORCIDs for the people in the roster CSV.

Run it from any computer that can reach inspirehep.net (no packages to install):

    python3 tools/lookup_ids.py people_roster_review.csv -o roster_with_ids.csv --contact you@fnal.gov

For each group member (in_group = yes or blank) with a missing INSPIRE ID or ORCID it searches
INSPIRE's author profiles by name, then ranks the candidates: the family name must match, the
given name should match (nicknames like Jim/James count), and a Fermilab position or CMS
membership decides between people with the same name.

The output is a copy of the roster with suggestion columns added. Only "high" confidence
matches are copied into the inspire_id and orcid columns, and only where those were empty.
Review the file (sort by match_confidence) before loading it with import_roster; the extra
columns are ignored by the importer.
"""
import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

INSPIRE_AUTHORS = "https://inspirehep.net/api/authors"
ORCID_SEARCH = "https://pub.orcid.org/v3.0/expanded-search/"
FERMILAB = re.compile(r"fermilab|\bfnal\b|fermi national", re.I)
NICKNAMES = [
    {"jim", "james", "jimmy"}, {"doug", "douglas"}, {"liz", "elizabeth", "beth"}, {"nick", "nicholas", "nicolas"},
    {"jeff", "jeffrey", "geoffrey"}, {"don", "donald"}, {"steve", "stephen", "steven"}, {"mike", "michael"},
    {"dan", "daniel"}, {"jen", "jennifer", "jenny"}, {"ron", "ronald"}, {"burt", "burton"}, {"dave", "david"},
    {"rob", "robert", "bob"}, {"chris", "christopher"}, {"matt", "matthew"}, {"tom", "thomas"},
    {"allie", "allison", "alison", "alexandra"}, {"cristian", "christian"}, {"pat", "patrick"},
    {"sasha", "alexander", "aleksandr"}, {"tony", "anthony"}, {"joe", "joseph"}, {"bill", "william"},
]
NEW_COLUMNS = ["match_confidence", "suggested_inspire_id", "suggested_orcid", "inspire_profile", "inspire_name",
               "fermilab_positions", "other_candidates", "match_notes"]


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z ]+", " ", text.replace("-", " ")).split()


def given_score(roster_given, cand_given):
    """3 = same given name or nickname, 1 = initials only, 0 = different."""
    if not roster_given or not cand_given:
        return 1
    for r in roster_given:
        for c in cand_given:
            if r == c or any(r in s and c in s for s in NICKNAMES):
                return 3
    if roster_given[0][0] == cand_given[0][0] and (len(roster_given[0]) == 1 or len(cand_given[0]) == 1):
        return 1
    return 0


def split_candidate_names(meta):
    """INSPIRE stores names as 'Family, Given'; also check listed variants."""
    names = []
    name = meta.get("name") or {}
    for value in [name.get("value")] + list(name.get("name_variants") or []):
        if value and "," in value:
            family, given = value.split(",", 1)
            names.append((norm(family), norm(given)))
    return names


def name_score(roster_name, meta):
    tokens = norm(roster_name)
    best = 0
    for family, given in split_candidate_names(meta):
        if family and all(t in tokens for t in family):
            roster_given = [t for t in tokens if t not in family]
            best = max(best, given_score(roster_given, given))
    return best


def ids_of(meta):
    out = {}
    for i in meta.get("ids") or []:
        out.setdefault(i.get("schema"), i.get("value"))
    return out


def fermilab_positions(meta):
    spans = []
    for p in meta.get("positions") or []:
        if FERMILAB.search(p.get("institution") or ""):
            start = p.get("start_date") or "?"
            end = "present" if p.get("current") else (p.get("end_date") or "?")
            spans.append(f"{start} to {end}" + (f" ({p['rank']})" if p.get("rank") else ""))
    return spans


def is_cms(meta):
    return any("CMS" in (m.get("name") or "") for m in meta.get("project_membership") or [])


class Client:
    def __init__(self, contact, pause):
        agent = "FNAL-group-publication-tracker roster lookup" + (f" (contact: {contact})" if contact else "")
        self.headers = {"Accept": "application/json", "User-Agent": agent}
        self.pause = pause

    def get(self, url, params):
        full = f"{url}?{urllib.parse.urlencode(params)}"
        for attempt in range(3):
            time.sleep(self.pause)    # INSPIRE allows roughly 15 requests per 5 seconds
            try:
                with urllib.request.urlopen(urllib.request.Request(full, headers=self.headers), timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise


def inspire_candidates(client, name):
    data = client.get(INSPIRE_AUTHORS, {"q": name, "size": 25, "sort": "bestmatch"})
    return [h.get("metadata") or {} for h in (data.get("hits") or {}).get("hits") or []]


def orcid_search(client, family, given):
    q = (f'family-name:"{family}" AND given-names:"{given}" AND '
         f'(affiliation-org-name:"Fermi National Accelerator Laboratory" OR affiliation-org-name:Fermilab)')
    data = client.get(ORCID_SEARCH, {"q": q, "rows": 5})
    return data.get("expanded-result") or []


def rank(name, candidates):
    scored = []
    for meta in candidates:
        ns = name_score(name, meta)
        if ns == 0:
            continue
        fnal = fermilab_positions(meta)
        score = ns + (4 if fnal else 0) + (1 if any("present" in s for s in fnal) else 0) + (1 if is_cms(meta) else 0)
        scored.append((score, ns, bool(fnal), meta))
    scored.sort(key=lambda s: -s[0])
    return scored


def decide(scored):
    if not scored:
        return "none", None, "No INSPIRE profile with this name."
    top_score, top_name, top_fnal, top = scored[0]
    rivals_fnal = [s for s in scored[1:] if s[2]]
    if top_fnal and top_name == 3 and not rivals_fnal:
        return "high", top, ""
    if top_fnal and rivals_fnal:
        return "medium", top, f"{len(rivals_fnal) + 1} profiles with this name have Fermilab positions; check which is right."
    if top_fnal:
        return "medium", top, "Only the initials match; check the given name."
    return "low", top, "Name matches but no Fermilab position on INSPIRE; may be a different person."


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roster", help="people_roster_review.csv")
    ap.add_argument("-o", "--output", required=True, help="where to write the roster with suggestions")
    ap.add_argument("--contact", default="", help="your email, sent to INSPIRE in the User-Agent (polite)")
    ap.add_argument("--only", help="look up one person by id, e.g. --only kevin-pedro (for a quick test)")
    ap.add_argument("--no-orcid-search", action="store_true", help="skip the ORCID fallback search")
    ap.add_argument("--pause", type=float, default=0.4, help="seconds between requests")
    args = ap.parse_args()

    with open(args.roster, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields, rows = list(reader.fieldnames), list(reader)
    col = {k.split(" ")[0].strip().lower(): k for k in fields}
    g_col, i_col, o_col = col.get("in_group"), col.get("inspire_id"), col.get("orcid")
    client = Client(args.contact, args.pause)
    counts = {}

    for row in rows:
        for c in NEW_COLUMNS:
            row[c] = ""
        if args.only and row.get("id") != args.only:
            continue
        if (row.get(g_col) or "").strip().lower() in {"no", "n", "false", "0"}:
            row["match_notes"] = "Skipped: not in group."
            continue
        if (row.get(i_col) or "").strip() and (row.get(o_col) or "").strip():
            row["match_notes"] = "Skipped: already has both IDs."
            continue
        name = row["name"].strip()
        print(f"{name} ...", end=" ", flush=True, file=sys.stderr)
        try:
            scored = rank(name, inspire_candidates(client, name))
        except Exception as e:  # keep going; one failure shouldn't stop the run
            row["match_confidence"], row["match_notes"] = "error", f"INSPIRE lookup failed: {e}"
            print("lookup failed", file=sys.stderr)
            continue
        confidence, meta, note = decide(scored)
        row["match_confidence"], row["match_notes"] = confidence, note
        row["other_candidates"] = str(max(len(scored) - 1, 0))
        if meta:
            ids = ids_of(meta)
            row["suggested_inspire_id"] = ids.get("INSPIRE BAI") or ids.get("INSPIRE ID") or ""
            row["suggested_orcid"] = ids.get("ORCID") or ""
            row["inspire_name"] = (meta.get("name") or {}).get("value", "")
            if meta.get("control_number"):
                row["inspire_profile"] = f"https://inspirehep.net/authors/{meta['control_number']}"
            row["fermilab_positions"] = "; ".join(fermilab_positions(meta))

        if not row["suggested_orcid"] and not args.no_orcid_search and confidence in {"high", "medium", "none"}:
            tokens = name.split()
            try:
                hits = orcid_search(client, tokens[-1], tokens[0])
            except Exception as e:
                hits, row["match_notes"] = [], (row["match_notes"] + f" ORCID search failed: {e}").strip()
            if len(hits) == 1:
                row["suggested_orcid"] = hits[0].get("orcid-id", "")
                row["match_notes"] = (row["match_notes"] + " ORCID found by ORCID search with a Fermilab "
                                      "affiliation, not linked on INSPIRE: confirm with the person.").strip()
            elif len(hits) > 1:
                row["match_notes"] = (row["match_notes"] + f" ORCID search found {len(hits)} Fermilab people with "
                                      "this name.").strip()

        if confidence == "high":    # only fill empty cells, and only from the INSPIRE profile itself
            filled = []
            if i_col and not row[i_col].strip() and row["suggested_inspire_id"]:
                row[i_col] = row["suggested_inspire_id"]; filled.append("inspire_id")
            orcid_from_inspire = ids_of(meta).get("ORCID")
            if o_col and not row[o_col].strip() and orcid_from_inspire:
                row[o_col] = orcid_from_inspire; filled.append("orcid")
            if filled:
                row["match_notes"] = (row["match_notes"] + f" Filled in: {', '.join(filled)}.").strip()
        counts[confidence] = counts.get(confidence, 0) + 1
        print(confidence, file=sys.stderr)

    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields + NEW_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    print(f"\nLooked up {sum(counts.values())} people: {summary or 'none'}. Wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
