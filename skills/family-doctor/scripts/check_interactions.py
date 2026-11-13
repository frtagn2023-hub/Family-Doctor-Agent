#!/usr/bin/env python3
"""LIMITED DEMO only: exact drug aliases and explicit pair rules, not a safety check.

Rules are inherited from this project's demonstration examples, not a maintained
clinical database. A missing match ALWAYS means unknown, never safe.
"""
import argparse
import itertools
import json

from health_store import MEDICATIONS_FILE, ensure_self, load_json, normalize_medications

ALIASES = {
    "warfarin": ["华法林"], "aspirin": ["阿司匹林"], "ibuprofen": ["布洛芬"],
    "naproxen": ["萘普生"], "amiodarone": ["胺碘酮"], "metronidazole": ["甲硝唑"],
    "lisinopril": ["赖诺普利"], "potassium supplements": ["钾补充剂"],
    "spironolactone": ["螺内酯"], "losartan": ["氯沙坦"], "irbesartan": ["厄贝沙坦"],
    "metformin": ["二甲双胍"], "contrast dye": ["造影剂"], "cimetidine": ["西咪替丁"],
    "furosemide": ["呋塞米"], "atorvastatin": ["阿托伐他汀"], "gemfibrozil": ["吉非贝齐"],
    "clarithromycin": ["克拉霉素"], "itraconazole": ["伊曲康唑"], "cyclosporine": ["环孢素"],
    "simvastatin": ["辛伐他汀"], "verapamil": ["维拉帕米"], "digoxin": ["地高辛"],
    "quinidine": ["奎尼丁"], "diltiazem": ["地尔硫卓"], "erythromycin": ["红霉素"],
    "alcohol": ["酒精"], "dihydropyridines": ["地平类"], "statins": ["他汀类"],
}


def normalized_name(value):
    return " ".join(str(value).strip().casefold().split())


ALIAS_LOOKUP = {normalized_name(alias): drug for drug, aliases in ALIASES.items() for alias in [drug] + aliases}
RULES = {}


def _add(drug, others, severity, effect):
    for other in others:
        RULES[frozenset((drug, other))] = {"severity": severity, "effect": effect}


_add("warfarin", ["aspirin", "ibuprofen", "naproxen", "amiodarone", "metronidazole"], "major", "演示规则提示出血风险增加")
_add("lisinopril", ["potassium supplements", "spironolactone", "losartan"], "moderate", "演示规则提示血钾升高风险")
_add("metformin", ["contrast dye", "cimetidine", "furosemide"], "moderate", "演示规则提示需核对肾功能和相关不良反应")
_add("atorvastatin", ["gemfibrozil", "clarithromycin", "itraconazole", "cyclosporine"], "major", "演示规则提示肌损伤风险")
_add("simvastatin", ["gemfibrozil", "clarithromycin", "amiodarone", "verapamil"], "major", "演示规则提示肌损伤风险")
_add("digoxin", ["amiodarone", "verapamil", "quinidine"], "major", "演示规则提示地高辛毒性风险")
_add("aspirin", ["ibuprofen"], "review_required", "演示规则提示需核对阿司匹林的心脏保护作用")
_add("irbesartan", ["potassium supplements"], "review_required", "演示规则提示血钾升高风险")
_add("dihydropyridines", ["diltiazem"], "review_required", "宽泛药物类别演示规则：需核对具体药品及血压影响")
_add("statins", ["erythromycin"], "review_required", "宽泛药物类别演示规则：需核对具体药品及肌损伤风险")
_add("metformin", ["alcohol"], "review_required", "演示规则提示需核对乳酸酸中毒风险")

NOTICE = "LIMITED DEMO：仅覆盖少量演示规则，无临床验证；未命中一律为未知，不能证明可安全合用。请由医生或药师核实，勿据此自行调整用药。"


def evaluate_pair(first, second):
    drug1, drug2 = ALIAS_LOOKUP.get(normalized_name(first)), ALIAS_LOOKUP.get(normalized_name(second))
    rule = RULES.get(frozenset((drug1, drug2))) if drug1 and drug2 else None
    result = {"drug1": first, "drug2": second, "normalized_drug1": drug1,
              "normalized_drug2": drug2, "assessment": "potential_interaction" if rule else "unknown",
              "coverage": "limited_demo", "reason": "演示规则命中，需专业核实" if rule else
              ("药名未识别，无法评估" if not drug1 or not drug2 else "此药物组合不在演示规则覆盖范围，无法评估")}
    if rule:
        result.update(rule)
    return result


def evaluate_current(meds, new=None):
    active = [m for m in meds if m["status"] == "active"]
    pairs = [(new, m["name"]) for m in active] if new else list(itertools.combinations([m["name"] for m in active], 2))
    return {"notice": NOTICE, "assessment": "unknown", "active_medications": active,
            "pairs": [evaluate_pair(a, b) for a, b in pairs]}


def print_result(result):
    print(result["notice"])
    if not result["pairs"]:
        print("没有可比较的药物组合，无法评估。")
    for pair in result["pairs"]:
        print("{} + {}：{}".format(pair["drug1"], pair["drug2"], pair.get("effect", pair["reason"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="有限演示药物规则；不提供安全合用结论")
    parser.add_argument("--new")
    parser.add_argument("--drug1")
    parser.add_argument("--drug2")
    parser.add_argument("--role", default="self")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        # Direct comparisons are also guarded: these scripts are the owner-record
        # tool boundary; a proxy conversation must use no personal record tools.
        ensure_self(args.role)
        if args.new and (args.drug1 or args.drug2):
            raise ValueError("--new 与 --drug1/--drug2 不能同时使用")
        if bool(args.drug1) != bool(args.drug2):
            raise ValueError("直接比较必须同时提供 --drug1 和 --drug2")
        if args.drug1:
            result = {"notice": NOTICE, "assessment": "unknown", "pairs": [evaluate_pair(args.drug1, args.drug2)]}
        else:
            data = normalize_medications(load_json(MEDICATIONS_FILE, {"medications": []}))
            result = evaluate_current(data["medications"], args.new)
        print(json.dumps(result, ensure_ascii=False, indent=2)) if args.json else print_result(result)
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))


if __name__ == "__main__":
    main()
