import math

from join_planner import createBasePlans, isSortedOn, planSortKey
from models import (
    AggregateFunction,
    Catalog,
    EvaluationPlan,
    PlanStep,
    Query,
    RelationInfo,
    SetOperator
)
from semantic_analyzer import (
    getAttributeKey,
    getOutputTypes,
    resolveAttribute
)
from statistics import (
    estimateAttributeRowSize,
    estimateResultBlocks,
    estimateRowsPerBlockFromRowSize,
    estimateSortCost
)


class UnionOperator:
    name = "UNION"

    def estimateRows(self, leftRows: int, rightRows: int,
                     intersectionRows: int) -> int:
        return leftRows + rightRows - intersectionRows


class IntersectionOperator:
    name = "INTERSECT"

    def estimateRows(self, leftRows: int, rightRows: int,
                     intersectionRows: int) -> int:
        return intersectionRows


class DifferenceOperator:
    name = "EXCEPT"

    def estimateRows(self, leftRows: int, rightRows: int,
                     intersectionRows: int) -> int:
        return max(0, leftRows - intersectionRows)


def getSetOperatorModel(operator: SetOperator):
    if operator == SetOperator.UNION:
        return UnionOperator()

    if operator == SetOperator.INTERSECT:
        return IntersectionOperator()

    return DifferenceOperator()


def addSortOperation(catalog: Catalog, query: Query,
                     plan: EvaluationPlan) -> None:
    relationInfo = plan.relationInfo
    orderTable, orderAttribute = resolveAttribute(
        catalog,
        query,
        query.orderBy
    )
    orderKey = getAttributeKey(orderTable, orderAttribute)
    alreadySorted = isSortedOn(relationInfo, orderKey)
    sortCost = (
        0
        if alreadySorted
        else estimateSortCost(
            relationInfo.blockCount,
            catalog.bufferBlocks
        )
    )
    description = (
        "Međurezultat je već sortiran po " + query.orderBy + "."
        if alreadySorted
        else "Spoljno sortiranje po " + query.orderBy + " "
        + query.orderDirection + "."
    )
    plan.totalCost += sortCost
    relationInfo.sortedBy = [orderKey]
    plan.steps.append(PlanStep(
        "Sortiranje",
        "Spoljno sort-merge sortiranje",
        description,
        relationInfo.rowCount,
        relationInfo.blockCount,
        sortCost,
        0,
        plan.totalCost,
        [relationInfo.rowCount],
        [relationInfo.blockCount],
        "0 ako je ulaz uredjen, inace spoljnoSortiranje(b, M)",
        "b=" + str(relationInfo.blockCount)
        + ", M=" + str(catalog.bufferBlocks)
    ))


def addProjectionOperation(catalog: Catalog, query: Query,
                           plan: EvaluationPlan) -> None:
    if query.selectAttributes == ["*"]:
        return

    relationInfo = plan.relationInfo
    selectedKeys = []

    for attributeReference in query.selectAttributes:
        table, attribute = resolveAttribute(
            catalog,
            query,
            attributeReference
        )
        selectedKeys.append(getAttributeKey(table, attribute))

    if set(selectedKeys) == relationInfo.availableAttributes:
        return

    selectedCount = len(selectedKeys)
    selectedKeySet = set(selectedKeys)
    newRowSize = estimateAttributeRowSize(
        relationInfo,
        selectedKeySet
    )
    rowsPerBlock = estimateRowsPerBlockFromRowSize(newRowSize)
    resultBlocks = estimateResultBlocks(
        relationInfo.rowCount,
        rowsPerBlock
    )
    operationCost = relationInfo.blockCount
    materializationCost = resultBlocks
    oldRows = relationInfo.rowCount
    oldBlocks = relationInfo.blockCount
    oldSortedBy = relationInfo.sortedBy.copy()
    projectedDistinct = {
        key: relationInfo.distinctValues[key]
        for key in selectedKeys
        if key in relationInfo.distinctValues
    }
    projectedOrder = (
        oldSortedBy
        if len(oldSortedBy) > 0 and oldSortedBy[0] in selectedKeys
        else []
    )
    plan.totalCost += operationCost + materializationCost
    plan.relationInfo = RelationInfo(
        relationInfo.tableNames.copy(),
        relationInfo.rowCount,
        resultBlocks,
        rowsPerBlock,
        1 / rowsPerBlock,
        projectedDistinct,
        selectedCount,
        projectedOrder,
        selectedKeySet,
        relationInfo.uniqueKeys & selectedKeySet,
        True,
        newRowSize,
        {
            key: value
            for key, value in relationInfo.attributeInfo.items()
            if key in selectedKeySet
        }
    )
    plan.steps.append(PlanStep(
        "Projekcija",
        "Projekcija bez uklanjanja duplikata",
        "SELECT bez DISTINCT čuva duplikate.",
        oldRows,
        resultBlocks,
        operationCost,
        materializationCost,
        plan.totalCost,
        [oldRows],
        [oldBlocks],
        "b(ulaz) + b(rezultat)",
        str(operationCost) + " + " + str(materializationCost)
    ))


def addAggregateOperation(catalog: Catalog, query: Query,
                          plan: EvaluationPlan) -> None:
    relationInfo = plan.relationInfo
    aggregateTexts = [
        item.aggregate.text
        for item in query.selectItems
        if item.aggregate is not None
    ]
    operationCost = relationInfo.blockCount
    materializationCost = 1
    plan.totalCost += operationCost + materializationCost
    plan.relationInfo = RelationInfo(
        relationInfo.tableNames.copy(),
        1,
        1,
        1,
        1,
        {},
        len(aggregateTexts),
        [],
        {text.lower() for text in aggregateTexts},
        {text.lower() for text in aggregateTexts},
        True,
        1
    )
    plan.steps.append(PlanStep(
        "Agregacija",
        "Jednoprolazna agregacija",
        "Računa: " + ", ".join(aggregateTexts) + ".",
        1,
        1,
        operationCost,
        materializationCost,
        plan.totalCost,
        [relationInfo.rowCount],
        [relationInfo.blockCount],
        "b(ulaz) + b(rezultat)",
        str(operationCost) + " + " + str(materializationCost)
    ))


def addFinalOperations(catalog: Catalog, query: Query,
                       plan: EvaluationPlan) -> None:
    hasAggregate = any(
        item.aggregate is not None
        for item in query.selectItems
    )

    if hasAggregate:
        addAggregateOperation(catalog, query, plan)
        return

    orderColumnSelected = False

    if query.orderBy is not None:
        orderTable, orderAttribute = resolveAttribute(
            catalog,
            query,
            query.orderBy
        )
        orderKey = getAttributeKey(orderTable, orderAttribute)

        if query.selectAttributes == ["*"]:
            orderColumnSelected = True
        else:
            for attributeReference in query.selectAttributes:
                table, attribute = resolveAttribute(
                    catalog,
                    query,
                    attributeReference
                )

                if getAttributeKey(table, attribute) == orderKey:
                    orderColumnSelected = True
                    break

    if query.orderBy is not None and not orderColumnSelected:
        addSortOperation(catalog, query, plan)

    addProjectionOperation(catalog, query, plan)

    if query.orderBy is not None and orderColumnSelected:
        addSortOperation(catalog, query, plan)


def createAllPlans(catalog: Catalog,
                   query: Query) -> list[EvaluationPlan]:
    """Pravi kompletne planove, uključujući završne operatore."""
    if query.setOperator is not None:
        return [createSetPlan(catalog, query)]

    plans = createBasePlans(catalog, query)

    for plan in plans:
        addFinalOperations(catalog, query, plan)

    plans.sort(key=planSortKey)
    return plans


def copyStep(step: PlanStep,
             cumulativeOffset: float = 0,
             sideName: str = "") -> PlanStep:
    description = step.description

    if sideName != "":
        description = sideName + " upit. " + description

    return PlanStep(
        step.operation,
        step.algorithm,
        description,
        step.estimatedRows,
        step.estimatedBlocks,
        step.operationCost,
        step.materializationCost,
        step.cumulativeCost + cumulativeOffset,
        step.inputRows.copy(),
        step.inputBlocks.copy(),
        step.formula,
        step.numericFormula
    )


def estimateSetRows(operator: SetOperator,
                    leftRows: int, rightRows: int) -> int:
    """Gruba procena bez statistike o preklapanju skupova."""
    intersectionRows = math.ceil(
        0.5 * min(leftRows, rightRows)
    )

    operatorModel = getSetOperatorModel(operator)
    return operatorModel.estimateRows(
        leftRows,
        rightRows,
        intersectionRows
    )


def createSetPlan(catalog: Catalog,
                  query: Query,
                  optimized=False) -> EvaluationPlan:
    if optimized:
        leftPlan, leftPlans = findOptimizedPlan(
            catalog,
            query.leftQuery
        )
        rightPlan, rightPlans = findOptimizedPlan(
            catalog,
            query.rightQuery
        )
    else:
        leftPlan, leftPlans = findBestPlan(catalog, query.leftQuery)
        rightPlan, rightPlans = findBestPlan(catalog, query.rightQuery)

    leftInfo = leftPlan.relationInfo
    rightInfo = rightPlan.relationInfo
    totalInputBlocks = leftInfo.blockCount + rightInfo.blockCount
    leftAlreadySorted = (
        len(query.leftQuery.selectAttributes) == 1
        and len(leftInfo.sortedBy) > 0
    )
    rightAlreadySorted = (
        len(query.rightQuery.selectAttributes) == 1
        and len(rightInfo.sortedBy) > 0
    )
    leftSortCost = (
        0
        if leftAlreadySorted
        else estimateSortCost(
            leftInfo.blockCount,
            catalog.bufferBlocks
        )
    )
    rightSortCost = (
        0
        if rightAlreadySorted
        else estimateSortCost(
            rightInfo.blockCount,
            catalog.bufferBlocks
        )
    )
    sortCost = (
        leftSortCost + rightSortCost + totalInputBlocks
    )
    hashCost = (
        totalInputBlocks
        if min(leftInfo.blockCount, rightInfo.blockCount)
        <= max(1, catalog.bufferBlocks - 2)
        else 3 * totalInputBlocks
    )
    algorithms = [
        (sortCost, "Skupovna operacija sortiranjem"),
        (hashCost, "Skupovna operacija heširanjem")
    ]
    operationCost, algorithm = min(
        algorithms,
        key=lambda item: (item[0], item[1])
    )
    rows = estimateSetRows(
        query.setOperator,
        leftInfo.rowCount,
        rightInfo.rowCount
    )
    rowsPerBlock = max(
        1,
        min(leftInfo.rowsPerBlock, rightInfo.rowsPerBlock)
    )
    blocks = estimateResultBlocks(rows, rowsPerBlock)
    materializationCost = blocks
    totalCost = (
        leftPlan.totalCost + rightPlan.totalCost
        + operationCost + materializationCost
    )
    plan = EvaluationPlan(
        leftPlan.tableOrder + rightPlan.tableOrder
    )
    plan.steps = [
        copyStep(step, 0, "Levi")
        for step in leftPlan.steps
    ] + [
        copyStep(step, leftPlan.totalCost, "Desni")
        for step in rightPlan.steps
    ]
    plan.steps.append(PlanStep(
        "Skupovna operacija",
        algorithm,
        query.setOperator.value
        + " uklanja duplikate. Pretpostavljeno preklapanje je 50%.",
        rows,
        blocks,
        operationCost,
        materializationCost,
        totalCost,
        [leftInfo.rowCount, rightInfo.rowCount],
        [leftInfo.blockCount, rightInfo.blockCount],
        "cena(levi) + cena(desni) + cena(algoritma) + b(rezultat)",
        str(leftPlan.totalCost) + " + " + str(rightPlan.totalCost)
        + " + " + str(operationCost) + " + "
        + str(materializationCost)
    ))
    plan.totalCost = totalCost
    plan.relationInfo = RelationInfo(
        leftInfo.tableNames + rightInfo.tableNames,
        rows,
        blocks,
        rowsPerBlock,
        1 / rowsPerBlock,
        {},
        len(getOutputTypes(catalog, query)),
        ["set.column1"]
        if algorithm == "Skupovna operacija sortiranjem"
        else [],
        {
            "column" + str(position)
            for position in range(
                len(getOutputTypes(catalog, query))
            )
        },
        set(),
        True,
        1 / rowsPerBlock
    )
    return plan


def findBestPlan(catalog: Catalog, query: Query):
    if query.setOperator is None:
        from physical_search import findDpBestPlan

        bestPlan, searchedPlans = findDpBestPlan(catalog, query)
        compatibilityPlans = createAllPlans(catalog, query)
        return bestPlan, compatibilityPlans

    plans = createAllPlans(catalog, query)

    if len(plans) == 0:
        raise ValueError("Nije pronađen nijedan plan.")

    return plans[0], plans


def findOptimizedPlan(catalog: Catalog, query: Query):
    """Nova pretraga sa ukljucenim potiskivanjem projekcija."""
    if query.setOperator is not None:
        plan = createSetPlan(catalog, query, True)
        return plan, [plan]

    from physical_search import findDpBestPlan

    return findDpBestPlan(catalog, query, True)
