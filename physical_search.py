import copy
import itertools
from dataclasses import dataclass

from access_paths import (
    applyAccessPlanToInfo,
    createAccessPlans,
    estimateSelectionInfo
)
from join_planner import (
    addAccessStep,
    createJoinCandidates
)
from logical_optimizer import (
    applyFusedProjection,
    getRequiredAttributes,
    projectRelationInfo
)
from models import Catalog, EvaluationPlan, PlanStep, Query
from operators import addFinalOperations
from semantic_analyzer import (
    expressionToText,
    getJoinExpression,
    getLocalExpression
)


@dataclass
class SingleTablePlan:
    plan: EvaluationPlan
    accessPlan: object


def fillAccessFormula(step: PlanStep, accessPlan) -> None:
    if accessPlan.indexName is None:
        step.formula = "b(tabela) + b(rezultat)"
    else:
        step.formula = "pristup indeksu + citanje podataka + b(rezultat)"

    step.numericFormula = (
        str(accessPlan.accessCost) + " + "
        + str(accessPlan.materializationCost)
    )


def createSingleTablePlans(catalog: Catalog, query: Query,
                           tableName: str,
                           pushProjections=False) -> list[SingleTablePlan]:
    """Pravi po jedan kandidat za svaki pristupni put tabele."""
    table = catalog.findTable(tableName)
    baseInfo = estimateSelectionInfo(catalog, query, table)
    result = []

    for accessPlan in createAccessPlans(catalog, query, table):
        plan = EvaluationPlan([table.name])
        plan.totalCost = accessPlan.totalCost
        plan.relationInfo = applyAccessPlanToInfo(
            baseInfo,
            accessPlan
        )
        addAccessStep(plan, accessPlan, plan.totalCost)
        fillAccessFormula(plan.steps[-1], accessPlan)
        localExpression = getLocalExpression(catalog, query, table)

        if localExpression is not None:
            plan.logicalTransformations.append(
                "Potisnuta selekcija nad " + table.name + ": "
                + expressionToText(localExpression)
            )

        if pushProjections:
            plan = applyFusedProjection(
                plan,
                getRequiredAttributes(
                    catalog,
                    query,
                    [table.name]
                )
            )

        result.append(SingleTablePlan(plan, accessPlan))

    return result


def copyStepWithOffset(step: PlanStep,
                       offset: float) -> PlanStep:
    result = copy.deepcopy(step)
    result.cumulativeCost += offset
    return result


def getJoinFormula(algorithm: str, leftInfo,
                   rightInfo, bufferBlocks: int) -> tuple[str, str]:
    outerInfo = leftInfo
    innerInfo = rightInfo

    if "desni spoljni" in algorithm:
        outerInfo = rightInfo
        innerInfo = leftInfo

    if algorithm.startswith("Blok ugnezdena"):
        formula = "bo + ceil(bo / (M - 2)) * bi"
        numeric = (
            "bo=" + str(outerInfo.blockCount)
            + ", bi=" + str(innerInfo.blockCount)
            + ", M=" + str(bufferBlocks)
        )
    elif algorithm.startswith("Spajanje ugnezdenom"):
        formula = "bo + no * bi"
        numeric = (
            "bo=" + str(outerInfo.blockCount)
            + ", no=" + str(outerInfo.rowCount)
            + ", bi=" + str(innerInfo.blockCount)
        )
    elif algorithm.startswith("Indeksirana"):
        formula = "bo + no * cenaJednogIndeksnogPristupa"
        numeric = (
            "bo=" + str(leftInfo.blockCount)
            + ", no=" + str(leftInfo.rowCount)
        )
    elif algorithm.startswith("Objedinjeno"):
        formula = "sort(R) + sort(S) + br + bs"
        numeric = (
            "br=" + str(leftInfo.blockCount)
            + ", bs=" + str(rightInfo.blockCount)
        )
    else:
        formula = "hashParticionisanje(br, bs, M)"
        buildBlocks = min(leftInfo.blockCount, rightInfo.blockCount)
        numeric = (
            "br=" + str(leftInfo.blockCount)
            + ", bs=" + str(rightInfo.blockCount)
            + ", M=" + str(bufferBlocks)
            + ", build=" + str(buildBlocks)
        )

    return formula, numeric


def extendPlan(catalog: Catalog, query: Query,
               leftPlan: EvaluationPlan,
               rightCandidate: SingleTablePlan,
               rightTableName: str,
               pushProjections=False) -> list[EvaluationPlan]:
    """Prosiruje parcijalni plan svim primenljivim join algoritmima."""
    rightTable = catalog.findTable(rightTableName)
    rightPlan = rightCandidate.plan
    rightAccess = rightCandidate.accessPlan
    joinExpression = getJoinExpression(
        catalog,
        query,
        leftPlan.relationInfo.tableNames,
        rightTable.name
    )
    resultInfo, joinCandidates = createJoinCandidates(
        catalog,
        query,
        leftPlan.relationInfo,
        rightPlan.relationInfo,
        leftPlan.totalCost,
        rightAccess,
        rightTable,
        joinExpression,
        rightPlan.totalCost
    )
    projectionDifference = 0

    if pushProjections:
        requiredAttributes = getRequiredAttributes(
            catalog,
            query,
            resultInfo.tableNames
        )
        projectedInfo = projectRelationInfo(
            resultInfo,
            requiredAttributes
        )
        projectionDifference = (
            projectedInfo.blockCount - resultInfo.blockCount
        )

    alternatives = "; ".join(
        candidate["algorithm"] + ": "
        + str(round(
            candidate["totalCost"] + projectionDifference,
            2
        ))
        for candidate in joinCandidates
    )
    result = []

    for candidate in joinCandidates:
        plan = EvaluationPlan(
            leftPlan.tableOrder + [rightTable.name]
        )
        plan.steps = copy.deepcopy(leftPlan.steps)
        plan.logicalTransformations = (
            leftPlan.logicalTransformations.copy()
            + rightPlan.logicalTransformations.copy()
        )

        if candidate["usesRightAccess"]:
            plan.steps.extend(
                copyStepWithOffset(step, leftPlan.totalCost)
                for step in rightPlan.steps
            )

        plan.totalCost = candidate["totalCost"]
        plan.relationInfo = copy.deepcopy(resultInfo)
        plan.relationInfo.sortedBy = candidate["sortedBy"].copy()
        operation = (
            "Kartezijanski proizvod"
            if joinExpression is None
            else "Spajanje"
        )
        formula, numericFormula = getJoinFormula(
            candidate["algorithm"],
            leftPlan.relationInfo,
            rightPlan.relationInfo,
            catalog.bufferBlocks
        )
        plan.steps.append(PlanStep(
            operation,
            candidate["algorithm"],
            candidate["description"]
            + " Razmotrene cene: " + alternatives + ".",
            plan.relationInfo.rowCount,
            plan.relationInfo.blockCount,
            candidate["operationCost"],
            candidate["materializationCost"],
            plan.totalCost,
            [
                leftPlan.relationInfo.rowCount,
                rightPlan.relationInfo.rowCount
            ],
            [
                leftPlan.relationInfo.blockCount,
                rightPlan.relationInfo.blockCount
            ],
            formula,
            numericFormula
        ))

        if pushProjections:
            plan = applyFusedProjection(
                plan,
                requiredAttributes
            )

        result.append(plan)

    return result


def getMaterializedBlocks(plan: EvaluationPlan) -> float:
    return sum(step.materializationCost for step in plan.steps)


def getSortCount(plan: EvaluationPlan) -> int:
    return sum(
        1 for step in plan.steps
        if step.operation == "Sortiranje" and step.operationCost > 0
    )


def getCrossProductCount(plan: EvaluationPlan) -> int:
    return sum(
        1 for step in plan.steps
        if step.operation == "Kartezijanski proizvod"
    )


def stablePlanDescription(plan: EvaluationPlan) -> str:
    return "|".join(
        step.operation + ":" + step.algorithm
        for step in plan.steps
    )


def finalPlanSortKey(plan: EvaluationPlan) -> tuple:
    """Stabilan tie-break za kompletne planove."""
    return (
        plan.totalCost,
        getMaterializedBlocks(plan),
        getSortCount(plan),
        getCrossProductCount(plan),
        len(plan.steps),
        stablePlanDescription(plan),
        tuple(name.lower() for name in plan.tableOrder)
    )


def samePhysicalProperty(first: EvaluationPlan,
                         second: EvaluationPlan) -> bool:
    firstInfo = first.relationInfo
    secondInfo = second.relationInfo
    return (
        frozenset(firstInfo.tableNames)
        == frozenset(secondInfo.tableNames)
        and tuple(firstInfo.sortedBy) == tuple(secondInfo.sortedBy)
        and firstInfo.availableAttributes
        == secondInfo.availableAttributes
    )


def dominates(first: EvaluationPlan,
              second: EvaluationPlan) -> bool:
    """Proverava dominaciju samo uz iste fizicke osobine."""
    if not samePhysicalProperty(first, second):
        return False

    noWorse = (
        first.totalCost <= second.totalCost
        and first.relationInfo.blockCount
        <= second.relationInfo.blockCount
        and getMaterializedBlocks(first)
        <= getMaterializedBlocks(second)
    )
    strictlyBetter = (
        first.totalCost < second.totalCost
        or first.relationInfo.blockCount
        < second.relationInfo.blockCount
        or getMaterializedBlocks(first)
        < getMaterializedBlocks(second)
    )
    return noWorse and strictlyBetter


def pruneDominated(plans: list[EvaluationPlan]) -> tuple[list, int]:
    kept = []
    rejected = 0

    for candidate in sorted(plans, key=finalPlanSortKey):
        if any(dominates(plan, candidate) for plan in kept):
            rejected += 1
            continue

        survivors = []

        for plan in kept:
            if dominates(candidate, plan):
                rejected += 1
            else:
                survivors.append(plan)

        survivors.append(candidate)
        kept = survivors

    return kept, rejected


def canConnect(catalog: Catalog, query: Query,
               includedTables: list[str], tableName: str) -> bool:
    return getJoinExpression(
        catalog,
        query,
        includedTables,
        tableName
    ) is not None


def shouldAllowExtension(catalog: Catalog, query: Query,
                         includedTables: list[str],
                         nextTable: str,
                         remainingTables: list[str]) -> bool:
    if canConnect(catalog, query, includedTables, nextTable):
        return True

    return not any(
        canConnect(catalog, query, includedTables, tableName)
        for tableName in remainingTables
        if tableName.lower() != nextTable.lower()
    )


def finishPlans(catalog: Catalog, query: Query,
                plans: list[EvaluationPlan],
                searchStatistics: dict[str, int]) -> list[EvaluationPlan]:
    result = []

    for partialPlan in plans:
        plan = copy.deepcopy(partialPlan)
        addFinalOperations(catalog, query, plan)
        plan.searchStatistics = searchStatistics.copy()
        result.append(plan)

    result.sort(key=finalPlanSortKey)
    return result


def generateDpPlans(catalog: Catalog,
                    query: Query,
                    pushProjections=False) -> list[EvaluationPlan]:
    """DP pretraga sa cuvanjem interesantnih uredjenja."""
    singlePlans = {
        tableName.lower(): createSingleTablePlans(
            catalog,
            query,
            tableName,
            pushProjections
        )
        for tableName in query.tableNames
    }
    states = {}
    generated = 0
    dominatedCount = 0
    inapplicable = 0

    for tableName in query.tableNames:
        key = frozenset([tableName.lower()])
        plans = [
            candidate.plan
            for candidate in singlePlans[tableName.lower()]
        ]
        states[key], rejected = pruneDominated(plans)
        generated += len(plans)
        dominatedCount += rejected

    for size in range(1, len(query.tableNames)):
        currentStates = [
            (key, plans)
            for key, plans in list(states.items())
            if len(key) == size
        ]
        additions = {}

        for key, plans in currentStates:
            remaining = [
                name for name in query.tableNames
                if name.lower() not in key
            ]

            for tableName in remaining:
                for leftPlan in plans:
                    if not shouldAllowExtension(
                        catalog,
                        query,
                        leftPlan.relationInfo.tableNames,
                        tableName,
                        remaining
                    ):
                        inapplicable += 1
                        continue

                    newKey = key | {tableName.lower()}

                    for rightCandidate in singlePlans[
                        tableName.lower()
                    ]:
                        newPlans = extendPlan(
                            catalog,
                            query,
                            leftPlan,
                            rightCandidate,
                            tableName,
                            pushProjections
                        )
                        generated += len(newPlans)
                        additions.setdefault(newKey, []).extend(newPlans)

        for key, plans in additions.items():
            combined = states.get(key, []) + plans
            states[key], rejected = pruneDominated(combined)
            dominatedCount += rejected

    finalKey = frozenset(name.lower() for name in query.tableNames)
    finalPartials = states.get(finalKey, [])
    statistics = {
        "generated": generated,
        "inapplicable": inapplicable,
        "dominated": dominatedCount,
        "kept": len(finalPartials)
    }
    return finishPlans(
        catalog,
        query,
        finalPartials,
        statistics
    )


def generateBruteForcePlans(catalog: Catalog,
                            query: Query,
                            pushProjections=False) -> list[EvaluationPlan]:
    """Referentna pretraga bez dominacije i pruning-a kandidata."""
    singlePlans = {
        tableName.lower(): createSingleTablePlans(
            catalog,
            query,
            tableName,
            pushProjections
        )
        for tableName in query.tableNames
    }
    completed = []
    generated = sum(len(value) for value in singlePlans.values())
    inapplicable = 0

    for tableOrder in itertools.permutations(query.tableNames):
        partials = [
            candidate.plan
            for candidate in singlePlans[tableOrder[0].lower()]
        ]

        for position, tableName in enumerate(tableOrder[1:], 1):
            nextPartials = []
            remaining = list(tableOrder[position:])

            for leftPlan in partials:
                if not shouldAllowExtension(
                    catalog,
                    query,
                    leftPlan.relationInfo.tableNames,
                    tableName,
                    remaining
                ):
                    inapplicable += 1
                    continue

                for rightCandidate in singlePlans[
                    tableName.lower()
                ]:
                    plans = extendPlan(
                        catalog,
                        query,
                        leftPlan,
                        rightCandidate,
                        tableName,
                        pushProjections
                    )
                    generated += len(plans)
                    nextPartials.extend(plans)

            partials = nextPartials

            if len(partials) == 0:
                break

        completed.extend(partials)

    statistics = {
        "generated": generated,
        "inapplicable": inapplicable,
        "dominated": 0,
        "kept": len(completed)
    }
    return finishPlans(catalog, query, completed, statistics)


def generateAllTableOrderPlans(catalog: Catalog,
                               query: Query) -> list[EvaluationPlan]:
    """Za prikaz nalazi najbolji fizicki plan svake permutacije."""
    singlePlans = {
        tableName.lower(): createSingleTablePlans(
            catalog,
            query,
            tableName,
            True
        )
        for tableName in query.tableNames
    }
    completed = []
    generated = sum(len(value) for value in singlePlans.values())
    dominatedCount = 0

    for tableOrder in itertools.permutations(query.tableNames):
        partials = [
            candidate.plan
            for candidate in singlePlans[tableOrder[0].lower()]
        ]

        for tableName in tableOrder[1:]:
            nextPartials = []

            for leftPlan in partials:
                for rightCandidate in singlePlans[
                    tableName.lower()
                ]:
                    plans = extendPlan(
                        catalog,
                        query,
                        leftPlan,
                        rightCandidate,
                        tableName,
                        True
                    )
                    generated += len(plans)
                    nextPartials.extend(plans)

            partials, rejected = pruneDominated(nextPartials)
            dominatedCount += rejected

        completed.extend(partials)

    statistics = {
        "generated": generated,
        "inapplicable": 0,
        "dominated": dominatedCount,
        "kept": len(completed)
    }
    return finishPlans(catalog, query, completed, statistics)


def findDpBestPlan(catalog: Catalog, query: Query,
                   pushProjections=False):
    plans = generateDpPlans(catalog, query, pushProjections)

    if len(plans) == 0:
        raise ValueError("Nije pronadjen nijedan poveziv plan.")

    return plans[0], plans


def findBruteForceBestPlan(catalog: Catalog, query: Query,
                           pushProjections=False):
    plans = generateBruteForcePlans(
        catalog,
        query,
        pushProjections
    )

    if len(plans) == 0:
        raise ValueError("Brute-force nije pronasao plan.")

    return plans[0], plans
