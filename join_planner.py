import itertools
import math

from access_paths import (
    applyAccessPlanToInfo,
    estimateSelectionInfo,
    findBestAccessPlan
)
from models import (
    Catalog,
    ComparisonExpression,
    Condition,
    IndexType,
    Literal,
    LogicalExpression,
    PlanStep,
    Query,
    RelationInfo
)
from semantic_analyzer import (
    containsOr,
    expressionToText,
    getAttributeKey,
    getConnectingConditions,
    getJoinExpression,
    getLocalConditions,
    getLocalExpression,
    isConstant,
    resolveAttribute
)
from sql_parser import (
    comparisonToCondition,
    flattenComparisons,
    parseWhereExpression
)
from statistics import (
    estimateJoinInfo,
    estimateResultBlocks,
    estimateSortCost
)
from models import EvaluationPlan


def conditionsToExpression(conditions: list[Condition]
                           ) -> LogicalExpression | None:
    if len(conditions) == 0:
        return None

    text = " AND ".join(
        condition.leftOperand + " " + condition.operator
        + " " + condition.rightOperand
        for condition in conditions
    )
    return parseWhereExpression(text)


def normalizeJoinExpression(value) -> LogicalExpression | None:
    if value is None:
        return None

    if isinstance(value, list):
        return conditionsToExpression(value)

    return value


def getConditionAttributeForTable(catalog: Catalog, query: Query,
                                  condition: Condition,
                                  tableName: str):
    leftTable, leftAttribute = resolveAttribute(
        catalog,
        query,
        condition.leftOperand
    )

    if leftTable.name.lower() == tableName.lower():
        return leftAttribute

    if isConstant(condition.rightOperand):
        return None

    rightTable, rightAttribute = resolveAttribute(
        catalog,
        query,
        condition.rightOperand
    )

    if rightTable.name.lower() == tableName.lower():
        return rightAttribute

    return None


def findJoinIndexes(catalog: Catalog, query: Query,
                    innerTable, joinConditions: list[Condition]):
    """Traži indeks unutrašnje tabele nad join atributom."""
    localEqualities = {}
    joinEqualities = {}

    localExpression = getLocalExpression(
        catalog,
        query,
        innerTable
    )

    if not containsOr(localExpression):
        for condition in getLocalConditions(catalog, query, innerTable):
            if condition.operator != "=" \
                    or not isConstant(condition.rightOperand):
                continue

            attribute = getConditionAttributeForTable(
                catalog,
                query,
                condition,
                innerTable.name
            )
            localEqualities[attribute.name.lower()] = condition

    for condition in joinConditions:
        if condition.operator != "=" \
                or isConstant(condition.rightOperand):
            continue

        attribute = getConditionAttributeForTable(
            catalog,
            query,
            condition,
            innerTable.name
        )

        if attribute is not None:
            joinEqualities[attribute.name.lower()] = condition

    available = set(localEqualities) | set(joinEqualities)
    result = []

    for index in innerTable.indexes:
        indexAttributes = [
            name.lower() for name in index.attributes
        ]

        if index.indexType == IndexType.HASH:
            if not all(name in available for name in indexAttributes):
                continue

            driving = next(
                (
                    name for name in indexAttributes
                    if name in joinEqualities
                ),
                None
            )

            if driving is not None:
                result.append((
                    index,
                    joinEqualities[driving],
                    innerTable.findAttribute(driving),
                    indexAttributes
                ))

            continue

        prefix = []

        for name in indexAttributes:
            if name not in available:
                break

            prefix.append(name)

        driving = next(
            (name for name in prefix if name in joinEqualities),
            None
        )

        if driving is not None:
            result.append((
                index,
                joinEqualities[driving],
                innerTable.findAttribute(driving),
                prefix
            ))

    return result


def estimateRowsPerIndexLookup(innerInfo: RelationInfo,
                               innerTable,
                               innerAttribute,
                               matchedAttributeNames: list[str]) -> int:
    selectivity = 1

    for name in matchedAttributeNames:
        attribute = innerTable.findAttribute(name)

        if attribute is None:
            continue

        if attribute.unique:
            return 1

        selectivity *= 1 / max(1, attribute.distinctValues)

    return max(1, math.ceil(innerTable.rowCount * selectivity))


def estimateIndexLookupCost(innerInfo: RelationInfo, innerTable,
                            innerAttribute, index,
                            matchedAttributeNames: list[str]) -> float:
    matchingRows = estimateRowsPerIndexLookup(
        innerInfo,
        innerTable,
        innerAttribute,
        matchedAttributeNames
    )
    dataTransfers = (
        estimateResultBlocks(
            matchingRows,
            innerTable.rowsPerBlock
        )
        if index.clustered
        else matchingRows
    )

    if index.indexType == IndexType.HASH:
        return 1.2 + dataTransfers

    return max(1, index.treeHeight or 1) + dataTransfers


def isSortedOn(relationInfo: RelationInfo,
               attributeKey: str) -> bool:
    return (
        len(relationInfo.sortedBy) > 0
        and relationInfo.sortedBy[0] == attributeKey
    )


def estimateHashJoinCost(leftBlocks: int, rightBlocks: int,
                         bufferBlocks: int) -> int:
    """Cena heš spajanja sa najviše nekoliko particionih prolaza."""
    buildBlocks = min(leftBlocks, rightBlocks)
    totalBlocks = leftBlocks + rightBlocks

    if buildBlocks <= max(1, bufferBlocks - 2):
        return totalBlocks

    fanout = max(2, bufferBlocks - 1)

    if buildBlocks <= fanout * fanout:
        return 3 * totalBlocks

    ratio = buildBlocks / max(1, bufferBlocks)
    partitionPasses = max(
        1,
        math.ceil(math.log(ratio, fanout))
    )
    return 2 * totalBlocks * partitionPasses + totalBlocks


def candidateSortKey(candidate: dict) -> tuple:
    """Daje isti izbor i kada dva plana imaju jednaku cenu."""
    algorithmOrder = {
        "Blok ugnezdena petlja": 0,
        "Hes spajanje": 1,
        "Objedinjeno spajanje": 2,
        "Spajanje ugnezdenom petljom": 3
    }
    algorithm = candidate["algorithm"]
    baseName = algorithm

    for knownName in algorithmOrder:
        if algorithm.startswith(knownName):
            baseName = knownName
            break

    if algorithm.startswith("Indeksirana"):
        baseName = "Indeksirana ugnezdena petlja"
    return (
        candidate["totalCost"],
        algorithmOrder.get(baseName, 4),
        algorithm
    )


def createJoinCandidates(catalog: Catalog, query: Query,
                         leftInfo: RelationInfo,
                         rightInfo: RelationInfo,
                         leftCost: float, rightAccessPlan,
                         rightTable, joinConditions,
                         rightCost=None):
    """Pravi primenljive fizičke algoritme jednog spajanja."""
    joinExpression = normalizeJoinExpression(joinConditions)
    resultInfo = estimateJoinInfo(
        catalog,
        query,
        leftInfo,
        rightInfo,
        joinExpression
    )
    materializationCost = resultInfo.blockCount
    if rightCost is None:
        rightCost = rightAccessPlan.totalCost

    commonCost = leftCost + rightCost
    conditionText = (
        "CROSS PRODUCT"
        if joinExpression is None
        else expressionToText(joinExpression)
    )
    candidates = []

    leftOuterCost = (
        leftInfo.blockCount
        + leftInfo.rowCount * rightInfo.blockCount
    )
    rightOuterCost = (
        rightInfo.blockCount
        + rightInfo.rowCount * leftInfo.blockCount
    )

    nestedDirections = [
        (
            "levi",
            leftOuterCost,
            ", ".join(leftInfo.tableNames),
            leftInfo.sortedBy.copy()
        ),
        (
            "desni",
            rightOuterCost,
            ", ".join(rightInfo.tableNames),
            rightInfo.sortedBy.copy()
        )
    ]

    for side, nestedCost, nestedOuter, nestedOrder in nestedDirections:
        candidates.append({
            "algorithm": (
                "Spajanje ugnezdenom petljom ("
                + side + " spoljni)"
            ),
            "operationCost": nestedCost,
            "materializationCost": materializationCost,
            "totalCost": commonCost + nestedCost + materializationCost,
            "usesRightAccess": True,
            "sortedBy": nestedOrder,
            "description": (
                conditionText + ". Spoljni ulaz: " + nestedOuter + "."
            )
        })

    outerBufferBlocks = max(1, catalog.bufferBlocks - 2)
    leftOuterCost = (
        leftInfo.blockCount
        + math.ceil(leftInfo.blockCount / outerBufferBlocks)
        * rightInfo.blockCount
    )
    rightOuterCost = (
        rightInfo.blockCount
        + math.ceil(rightInfo.blockCount / outerBufferBlocks)
        * leftInfo.blockCount
    )

    blockDirections = [
        (
            "levi",
            leftOuterCost,
            ", ".join(leftInfo.tableNames),
            leftInfo.sortedBy.copy()
        ),
        (
            "desni",
            rightOuterCost,
            ", ".join(rightInfo.tableNames),
            rightInfo.sortedBy.copy()
        )
    ]

    for side, blockCost, blockOuter, blockOrder in blockDirections:
        candidates.append({
            "algorithm": (
                "Blok ugnezdena petlja (" + side + " spoljni)"
            ),
            "operationCost": blockCost,
            "materializationCost": materializationCost,
            "totalCost": commonCost + blockCost + materializationCost,
            "usesRightAccess": True,
            "sortedBy": blockOrder,
            "description": (
                conditionText + ". Koristi "
                + str(outerBufferBlocks)
                + " blokova za spoljni ulaz. Spoljni ulaz: "
                + blockOuter + "."
            )
        })

    if joinExpression is None or containsOr(joinExpression):
        return resultInfo, candidates

    comparisons = flattenComparisons(joinExpression)
    equalityComparisons = [
        comparison for comparison in comparisons
        if (comparison.operator == "="
            and not isinstance(comparison.right, Literal))
    ]

    for comparison in equalityComparisons:
        firstTable, firstAttribute = resolveAttribute(
            catalog,
            query,
            comparison.left.text
        )
        secondTable, secondAttribute = resolveAttribute(
            catalog,
            query,
            comparison.right.text
        )
        firstKey = getAttributeKey(firstTable, firstAttribute)
        secondKey = getAttributeKey(secondTable, secondAttribute)
        leftNames = {name.lower() for name in leftInfo.tableNames}

        if firstTable.name.lower() in leftNames:
            leftKey, rightKey = firstKey, secondKey
        else:
            leftKey, rightKey = secondKey, firstKey

        leftSortCost = (
            0 if isSortedOn(leftInfo, leftKey)
            else estimateSortCost(
                leftInfo.blockCount,
                catalog.bufferBlocks
            )
        )
        rightSortCost = (
            0 if isSortedOn(rightInfo, rightKey)
            else estimateSortCost(
                rightInfo.blockCount,
                catalog.bufferBlocks
            )
        )
        mergeCost = (
            leftSortCost + rightSortCost
            + leftInfo.blockCount + rightInfo.blockCount
        )
        candidates.append({
            "algorithm": "Objedinjeno spajanje",
            "operationCost": mergeCost,
            "materializationCost": materializationCost,
            "totalCost": commonCost + mergeCost + materializationCost,
            "usesRightAccess": True,
            "sortedBy": [leftKey],
            "description": (
                conditionText + ". Sortiranje ulaza: "
                + str(leftSortCost) + " + " + str(rightSortCost) + "."
            )
        })

    if len(equalityComparisons) == 0:
        return resultInfo, candidates

    hashCost = estimateHashJoinCost(
        leftInfo.blockCount,
        rightInfo.blockCount,
        catalog.bufferBlocks
    )
    candidates.append({
        "algorithm": "Hes spajanje",
        "operationCost": hashCost,
        "materializationCost": materializationCost,
        "totalCost": commonCost + hashCost + materializationCost,
        "usesRightAccess": True,
        "sortedBy": [],
        "description": conditionText + ". Manji ulaz je build relacija."
    })

    equalityConditions = [
        comparisonToCondition(comparison)
        for comparison in equalityComparisons
    ]

    for index, condition, innerAttribute, matchedNames in findJoinIndexes(
        catalog,
        query,
        rightTable,
        equalityConditions
    ):
        lookupCost = estimateIndexLookupCost(
            rightInfo,
            rightTable,
            innerAttribute,
            index,
            matchedNames
        )
        indexedCost = (
            leftInfo.blockCount
            + leftInfo.rowCount * lookupCost
        )
        candidates.append({
            "algorithm": (
                "Indeksirana ugnezdena petlja - " + index.name
            ),
            "operationCost": indexedCost,
            "materializationCost": materializationCost,
            "totalCost": leftCost + indexedCost + materializationCost,
            "usesRightAccess": False,
            "sortedBy": [],
            "description": (
                conditionText + ". Cena jednog indeksnog pristupa: "
                + str(round(lookupCost, 2)) + "."
            )
        })

    return resultInfo, candidates


def addAccessStep(plan: EvaluationPlan, accessPlan,
                  cumulativeCost: float) -> None:
    operation = (
        "Selekcija"
        if len(accessPlan.usedConditions) > 0
        else "Pristup tabeli"
    )
    plan.steps.append(PlanStep(
        operation,
        accessPlan.algorithm,
        accessPlan.description,
        accessPlan.estimatedRows,
        accessPlan.estimatedBlocks,
        accessPlan.accessCost,
        accessPlan.materializationCost,
        cumulativeCost,
        [],
        []
    ))


def createPlanForOrder(catalog: Catalog, query: Query,
                       tableOrder) -> EvaluationPlan:
    """Pravi jedan levi-duboki plan za zadati redosled tabela."""
    plan = EvaluationPlan(list(tableOrder))
    firstTable = catalog.findTable(tableOrder[0])
    firstInfo = estimateSelectionInfo(catalog, query, firstTable)
    firstAccess = findBestAccessPlan(catalog, query, firstTable)
    currentInfo = applyAccessPlanToInfo(firstInfo, firstAccess)
    plan.totalCost = firstAccess.totalCost
    addAccessStep(plan, firstAccess, plan.totalCost)

    for tableName in tableOrder[1:]:
        rightTable = catalog.findTable(tableName)
        rightInfo = estimateSelectionInfo(catalog, query, rightTable)
        rightAccess = findBestAccessPlan(catalog, query, rightTable)
        rightInfo = applyAccessPlanToInfo(rightInfo, rightAccess)
        joinExpression = getJoinExpression(
            catalog,
            query,
            currentInfo.tableNames,
            rightTable.name
        )
        resultInfo, candidates = createJoinCandidates(
            catalog,
            query,
            currentInfo,
            rightInfo,
            plan.totalCost,
            rightAccess,
            rightTable,
            joinExpression
        )
        best = min(candidates, key=candidateSortKey)

        if best["usesRightAccess"]:
            addAccessStep(
                plan,
                rightAccess,
                plan.totalCost + rightAccess.totalCost
            )

        alternatives = "; ".join(
            candidate["algorithm"] + ": "
            + str(round(candidate["totalCost"], 2))
            for candidate in candidates
        )
        plan.totalCost = best["totalCost"]
        resultInfo.sortedBy = best["sortedBy"].copy()
        operation = (
            "Kartezijanski proizvod"
            if joinExpression is None
            else "Spajanje"
        )
        plan.steps.append(PlanStep(
            operation,
            best["algorithm"],
            best["description"]
            + " Razmotrene cene: " + alternatives + ".",
            resultInfo.rowCount,
            resultInfo.blockCount,
            best["operationCost"],
            best["materializationCost"],
            plan.totalCost,
            [currentInfo.rowCount, rightInfo.rowCount],
            [currentInfo.blockCount, rightInfo.blockCount]
        ))
        currentInfo = resultInfo

    plan.relationInfo = currentInfo
    return plan


def planSortKey(plan: EvaluationPlan) -> tuple:
    return (
        plan.totalCost,
        tuple(name.lower() for name in plan.tableOrder)
    )


def createBasePlans(catalog: Catalog,
                    query: Query) -> list[EvaluationPlan]:
    plans = [
        createPlanForOrder(catalog, query, tableOrder)
        for tableOrder in itertools.permutations(query.tableNames)
    ]
    plans.sort(key=planSortKey)
    return plans
