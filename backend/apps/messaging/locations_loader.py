"""Load the administrative tree (county -> sub-county -> ward, or any depth) into Location rows.

Two sources produce the same rows:
    CSV            code,name,level,parent_code[,sort_order]      (parent_code empty for the top level)
    wards.geojson  the project's ward boundaries, one feature per ward (county / sub-county / ward fields)

Loading is idempotent: rows are matched by ``code``, so re-running updates names, parents and ordering
without duplicating anything. Everything is validated before the first write, and applied in one
transaction, so a bad file changes nothing.
"""
import csv
import json
import re
from dataclasses import dataclass, field
from typing import List

from django.db import transaction

from apps.messaging.core.keywords import normalise_text
from apps.messaging.models import MAX_LOCATION_DEPTH, Location

CODE_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


class LoadError(ValueError):
    """The input cannot be loaded. ``errors`` lists every problem found, not just the first."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:5]))


@dataclass(frozen=True)
class Row:
    code: str
    name: str
    level: str
    parent_code: str = ""
    sort_order: int = 0


@dataclass
class Summary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- readers

def rows_from_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {"code", "name", "level", "parent_code"} - set(reader.fieldnames or [])
        if missing:
            raise LoadError([f"CSV is missing column(s): {', '.join(sorted(missing))}"])
        rows, errors = [], []
        for number, record in enumerate(reader, start=2):
            try:
                order = int((record.get("sort_order") or "0").strip() or 0)
            except ValueError:
                errors.append(f"line {number}: sort_order must be a whole number")
                continue
            rows.append(Row(
                code=(record["code"] or "").strip(), name=(record["name"] or "").strip(),
                level=(record["level"] or "").strip(), parent_code=(record["parent_code"] or "").strip(),
                sort_order=order,
            ))
    if errors:
        raise LoadError(errors)
    return rows


def _nice(name):
    """ALL-CAPS source names become Title Case; mixed-case names are left alone."""
    name = (name or "").strip()
    return name.title() if name.isupper() else name


def rows_from_wards_geojson(path, ward_code_field="uid"):
    """Build county, sub-county and ward rows from the project's wards.geojson.

    ward_code_field must be the property the data pipeline stores as ward_code (the pipeline falls
    back to ``uid`` for this file), or STATUS lookups will not find a recipient's ward data.
    """
    with open(path, encoding="utf-8") as handle:
        features = json.load(handle).get("features", [])
    rows, seen, errors = [], set(), []

    def add(code, name, level, parent):
        if code not in seen:
            seen.add(code)
            rows.append(Row(code=code, name=name, level=level, parent_code=parent))

    for index, feature in enumerate(features, start=1):
        props = feature.get("properties") or {}
        county_code = str(props.get("county_code_linked") or props.get("cuid") or "").strip()
        county_name = _nice(props.get("county_name_linked") or props.get("county"))
        sub_code = str(props.get("scuid") or "").strip()
        sub_name = _nice(props.get("subcounty"))
        ward_code = str(props.get(ward_code_field) or "").strip()
        ward_name = _nice(props.get("ward"))
        if not (county_code and county_name and sub_code and sub_name and ward_code and ward_name):
            errors.append(f"feature {index}: needs county, sub-county and ward names and codes (ward code field: {ward_code_field!r})")
            continue
        add(county_code, county_name, "county", "")
        add(sub_code, sub_name, "sub_county", county_code)
        add(ward_code, ward_name, "ward", sub_code)
    if errors:
        raise LoadError(errors)
    return rows


# ---------------------------------------------------------------- validation

def validate(rows):
    """Return a list of problems (empty = fine). Checks the tree as it will be after loading."""
    errors = []
    seen = set()
    for row in rows:
        if not CODE_PATTERN.match(row.code):
            errors.append(f"{row.code!r}: code must be 1-64 letters, digits, '.', '_' or '-'")
        if not row.name or len(row.name) > 150:
            errors.append(f"{row.code}: name is required (150 characters at most)")
        if not row.level or len(row.level) > 32:
            errors.append(f"{row.code}: level is required (32 characters at most)")
        if row.code in seen:
            errors.append(f"{row.code}: appears more than once")
        seen.add(row.code)
    if errors:
        return errors

    # The tree after loading = what is in the database, overlaid by these rows.
    final = {
        code: (parent_code or "", normalise_text(name))
        for code, parent_code, name in Location.objects.values_list("code", "parent__code", "name")
    }
    for row in rows:
        final[row.code] = (row.parent_code, normalise_text(row.name))

    for row in rows:
        if row.parent_code and row.parent_code not in final:
            errors.append(f"{row.code}: parent {row.parent_code!r} does not exist")
        if row.parent_code == row.code:
            errors.append(f"{row.code}: cannot be its own parent")
    if errors:
        return errors

    for row in rows:
        depth, node, visited = 0, row.code, set()
        while final[node][0]:
            if node in visited:
                errors.append(f"{row.code}: parents form a loop")
                break
            visited.add(node)
            node = final[node][0]
            depth += 1
        else:
            if depth + 1 > MAX_LOCATION_DEPTH:
                errors.append(f"{row.code}: more than {MAX_LOCATION_DEPTH} levels deep")

    siblings = {}
    for code, (parent, search_name) in final.items():
        if search_name in siblings.setdefault(parent, {}):
            errors.append(
                f"{code} and {siblings[parent][search_name]}: same name under the same parent "
                "(people could not tell them apart by SMS)"
            )
        siblings[parent][search_name] = code
    return errors


# ---------------------------------------------------------------- apply

def _depth_of(row, by_code):
    depth, node = 0, row
    while node.parent_code and node.parent_code in by_code:
        node = by_code[node.parent_code]
        depth += 1
    return depth


def load(rows, *, deactivate_missing=False, dry_run=False):
    """Validate, then upsert all rows in one transaction. Returns a Summary."""
    problems = validate(rows)
    if problems:
        raise LoadError(problems)

    by_code = {row.code: row for row in rows}
    ordered = sorted(rows, key=lambda r: _depth_of(r, by_code))
    summary = Summary()
    with transaction.atomic():
        nodes = {node.code: node for node in Location.objects.all()}
        for row in ordered:
            parent = nodes.get(row.parent_code) if row.parent_code else None
            node = nodes.get(row.code)
            if node is None:
                node = Location(
                    code=row.code, name=row.name, level=row.level, parent=parent,
                    sort_order=row.sort_order, is_active=True,
                )
                node.save()
                summary.created += 1
            else:
                changed = (
                    node.name != row.name or node.level != row.level or node.parent_id != (parent.pk if parent else None)
                    or node.sort_order != row.sort_order or not node.is_active
                )
                if changed:
                    node.name, node.level, node.parent = row.name, row.level, parent
                    node.sort_order, node.is_active = row.sort_order, True
                    node.save()
                    summary.updated += 1
                else:
                    summary.unchanged += 1
            nodes[row.code] = node

        if summary.updated:
            Location.rebuild_paths()  # a re-parented node must carry its whole subtree with it
        if deactivate_missing:
            summary.deactivated = Location.objects.filter(is_active=True).exclude(code__in=by_code).update(is_active=False)

        non_gsm = [r.code for r in rows if not r.name.isascii()]
        if non_gsm:
            summary.warnings.append(
                f"{len(non_gsm)} name(s) contain non-ASCII characters; an SMS containing them is limited to 70 characters per part."
            )
        if dry_run:
            transaction.set_rollback(True)
    return summary
