import math

from models import (
    AccessPlan,
    AggregateFunction,
    AndExpression,
    Catalog,
    IndexType,
    Query,
    RelationInfo,
    TableStats
)
from semantic_analyzer import (
    containsOr,
    expressionToText,
    getAttributeKey,
    getLocalConditions,
    getLocalExpression,
    isConstant,
    resolveAttribute
)
from sql_parser import parseWhereExpression
from statistics import (
    createSelectionRelationInfo,
    estimateExpressionSelectivity,
    estimateResultBlocks,
    estimateSelectionRows
)


def copyRelationInfo(relationInfo: RelationInfo) -> RelationInfo:
    return RelationInfo(
        relationInfo.tableNames.copy(),
        relationInfo.rowCount,
        relationInfo.blockCount,
        relationInfo.rowsPerBlock,
        relationInfo.rowBlockFraction,
        relationInfo.distinctValues.copy(),
        relationInfo.attributeCount,
        relationInfo.sortedBy.copy(),
        relationInfo.availableAttributes.copy(),
        relationInfo.uniqueKeys.copy(),
        relationInfo.materialized,
        relationInfo.rowSize,
        relationInfo.attributeInfo.copy()
    )


def getPhysicalTableOrder(table: TableStats) -> list[str]:
    """Vraća redosled koji daje grupišući B+ indeks."""
    for index in table.indexes:
        if (index.indexType == IndexType.B_PLUS_TREE
                and index.clustered
                and len(index.attributes) > 0):
            return [
                table.name.lower() + "." + name.lower()
                for name in index.attributes
            ]

    return []


def estimateSelectionInfo(catalog: Catalog, query: Query,
                          table: TableStats) -> RelationInfo:
    relationInfo = createSelectionRelationInfo(catalog, query, table)
    relationInfo.sortedBy = getPhysicalTableOrder(table)
    localExpression = getLocalExpression(catalog, query, table)

    if localExpression is None or containsOr(localExpression):
        return relationInfo

    for condition in getLocalConditions(catalog, query, table):
        leftTable, leftAttribute = resolveAttribute(
            catalog,
            query,
            condition.leftOperand
        )
        key = getAttributeKey(leftTable, leftAttribute)

        if condition.operator == "=" \
                and isConstant(condition.rightOperand):
            relationInfo.distinctValues[key] = 1

    return relationInfo


def estimateRowsForConditions(catalog: Catalog, query: Query,
                              table: TableStats,
                              conditions) -> int:
    """Stari interfejs za procenu konjunkcije liste uslova."""
    if len(conditions) == 0:
        return table.rowCount

    text = " AND ".join(
        condition.leftOperand + " " + condition.operator
        + " " + condition.rightOperand
        for condition in conditions
    )
    expression = parseWhereExpression(text)
    selectivity = estimateExpressionSelectivity(
        catalog,
        query,
        expression
    )

    if table.rowCount == 0:
        return 0

    return max(1, math.ceil(table.rowCount * selectivity))


def estimateLinearScanCost(catalog: Catalog, query: Query,
                           table: TableStats) -> int:
    localExpression = getLocalExpression(catalog, query, table)

    if not containsOr(localExpression):
        for condition in getLocalConditions(catalog, query, table):
            leftTable, leftAttribute = resolveAttribute(
                catalog,
                query,
                condition.leftOperand
            )

            if (condition.operator == "="
                    and leftAttribute.unique
                    and isConstant(condition.rightOperand)):
                return math.ceil(table.blockCount / 2)

    return table.blockCount


def estimateMaterializationCost(resultBlocks: int) -> int:
    return resultBlocks


def findAttributeConditions(catalog: Catalog, query: Query,
                            table: TableStats,
                            attributeName: str):
    found = []

    for condition in getLocalConditions(catalog, query, table):
        leftTable, leftAttribute = resolveAttribute(
            catalog,
            query,
            condition.leftOperand
        )

        if (leftAttribute.name.lower() == attributeName.lower()
                and isConstant(condition.rightOperand)):
            found.append(condition)

    return found


def findUsableIndexConditions(catalog: Catalog, query: Query,
                              table: TableStats, index):
    """Primenjuje pravilo levog prefiksa i HASH celog ključa."""
    localExpression = getLocalExpression(catalog, query, table)

    if localExpression is None or containsOr(localExpression):
        return []

    usedConditions = []

    if index.indexType == IndexType.HASH:
        for attributeName in index.attributes:
            equality = next(
                (
                    condition
                    for condition in findAttributeConditions(
                        catalog,
                        query,
                        table,
                        attributeName
                    )
                    if condition.operator == "="
                ),
                None
            )

            if equality is None:
                return []

            usedConditions.append(equality)

        return usedConditions

    if index.indexType == IndexType.B_PLUS_TREE:
        for attributeName in index.attributes:
            attributeConditions = findAttributeConditions(
                catalog,
                query,
                table,
                attributeName
            )
            equality = next(
                (
                    condition for condition in attributeConditions
                    if condition.operator == "="
                ),
                None
            )
            ranges = [
                condition for condition in attributeConditions
                if condition.operator in ["<", "<=", ">", ">="]
            ]

            if equality is not None:
                usedConditions.append(equality)
            elif len(ranges) > 0:
                usedConditions.extend(ranges)
                break
            else:
                break

    return usedConditions


def findUsableIndexes(catalog: Catalog, query: Query,
                      table: TableStats):
    result = []

    for index in table.indexes:
        conditions = findUsableIndexConditions(
            catalog,
            query,
            table,
            index
        )

        if len(conditions) > 0:
            result.append((index, conditions))

    return result


def conditionToText(condition) -> str:
    return (
        condition.leftOperand + " " + condition.operator
        + " " + condition.rightOperand
    )


def getIndexSelectionAlgorithm(index, usedConditions) -> str:
    hasRange = any(
        condition.operator in ["<", "<=", ">", ">="]
        for condition in usedConditions
    )
    isComposite = (
        len(index.attributes) > 1
        and len(usedConditions) > 1
    )

    if isComposite:
        return (
            "A8 - kompozitni "
            + (
                "B+ indeks"
                if index.indexType == IndexType.B_PLUS_TREE
                else "HASH indeks"
            )
        )

    if index.indexType == IndexType.HASH:
        return "A7 - HASH indeks"

    if hasRange:
        return (
            "A5 - grupisuci B+ indeks"
            if index.clustered
            else "A6 - sekundarni B+ indeks"
        )

    return (
        "A2/A3 - grupisuci B+ indeks"
        if index.clustered
        else "A4 - sekundarni B+ indeks"
    )


def estimateIndexAccessCost(catalog: Catalog, query: Query,
                            table: TableStats, index,
                            usedConditions) -> float:
    candidateRows = estimateRowsForConditions(
        catalog,
        query,
        table,
        usedConditions
    )
    dataTransfers = (
        estimateResultBlocks(candidateRows, table.rowsPerBlock)
        if index.clustered
        else candidateRows
    )

    if index.indexType == IndexType.HASH:
        return 1.2 + dataTransfers

    return max(1, index.treeHeight or 1) + dataTransfers


def createAccessPlans(catalog: Catalog, query: Query,
                      table: TableStats) -> list[AccessPlan]:
    relationInfo = estimateSelectionInfo(catalog, query, table)
    localConditions = getLocalConditions(catalog, query, table)
    localExpression = getLocalExpression(catalog, query, table)

    if localExpression is None:
        plans = [
            AccessPlan(
                table.name,
                "A1 - linearno skeniranje",
                None,
                relationInfo.rowCount,
                relationInfo.blockCount,
                table.blockCount,
                table.blockCount,
                [],
                getPhysicalTableOrder(table),
                "Cela tabela se čita i materijalizuje."
            )
        ]

        if len(query.selectItems) == 1:
            aggregate = query.selectItems[0].aggregate

            if (aggregate is not None
                    and aggregate.function in [
                        AggregateFunction.MIN,
                        AggregateFunction.MAX
                    ]
                    and aggregate.argument != "*"):
                aggregateTable, aggregateAttribute = resolveAttribute(
                    catalog,
                    query,
                    aggregate.argument
                )

                if aggregateTable.name.lower() == table.name.lower():
                    for index in table.indexes:
                        if (index.indexType == IndexType.B_PLUS_TREE
                                and index.attributes[0].lower()
                                == aggregateAttribute.name.lower()):
                            plans.append(AccessPlan(
                                table.name,
                                aggregate.function.value
                                + " preko B+ indeksa",
                                index.name,
                                1,
                                1,
                                index.treeHeight + 1,
                                1,
                                [],
                                [
                                    table.name.lower() + "."
                                    + aggregateAttribute.name.lower()
                                ],
                                "Direktan pristup krajnjem listu B+ stabla."
                            ))

        return plans

    materializationCost = relationInfo.blockCount
    plans = [
        AccessPlan(
            table.name,
            "A1 - linearno skeniranje",
            None,
            relationInfo.rowCount,
            relationInfo.blockCount,
            estimateLinearScanCost(catalog, query, table),
            materializationCost,
            localConditions,
            getPhysicalTableOrder(table),
            "Proverava izraz: "
            + expressionToText(localExpression) + "."
        )
    ]

    for index, usedConditions in findUsableIndexes(
        catalog,
        query,
        table
    ):
        sortedBy = []

        if (index.indexType == IndexType.B_PLUS_TREE
                and index.clustered):
            sortedBy = [
                table.name.lower() + "." + name.lower()
                for name in index.attributes
            ]

        plans.append(
            AccessPlan(
                table.name,
                getIndexSelectionAlgorithm(index, usedConditions),
                index.name,
                relationInfo.rowCount,
                relationInfo.blockCount,
                estimateIndexAccessCost(
                    catalog,
                    query,
                    table,
                    index,
                    usedConditions
                ),
                materializationCost,
                usedConditions,
                sortedBy,
                "Indeks koristi: " + ", ".join(
                    conditionToText(condition)
                    for condition in usedConditions
                ) + "."
            )
        )

    return plans


def findBestAccessPlan(catalog: Catalog, query: Query,
                       table: TableStats) -> AccessPlan:
    return min(
        createAccessPlans(catalog, query, table),
        key=lambda plan: (plan.totalCost, plan.algorithm)
    )


def applyAccessPlanToInfo(relationInfo: RelationInfo,
                          accessPlan: AccessPlan) -> RelationInfo:
    result = copyRelationInfo(relationInfo)
    result.rowCount = accessPlan.estimatedRows
    result.blockCount = accessPlan.estimatedBlocks
    result.sortedBy = accessPlan.sortedBy.copy()

    for key in result.distinctValues:
        result.distinctValues[key] = min(
            result.distinctValues[key],
            max(1, result.rowCount)
        )

    return result
