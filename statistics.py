import math

from models import (
    AndExpression,
    AttributeInfo,
    Catalog,
    ComparisonExpression,
    Literal,
    LogicalExpression,
    OrExpression,
    Query,
    RelationInfo,
    TableStats
)
from semantic_analyzer import (
    containsOr,
    getAttributeKey,
    getLocalExpression,
    resolveAttribute
)
from sql_parser import flattenComparisons, parseWhereExpression


def clamp(value: float, minimum: float = 0,
          maximum: float = 1) -> float:
    return max(minimum, min(maximum, value))


def estimateResultBlocks(rowCount: int,
                         rowsPerBlock: int) -> int:
    """Računa najmanji broj blokova za zadati broj redova."""
    if rowCount <= 0:
        return 0

    return math.ceil(rowCount / max(1, rowsPerBlock))


def createAttributeInfo(table: TableStats) -> dict[str, AttributeInfo]:
    """Pravi podatke o poreklu i relativnoj sirini atributa."""
    attributeCount = max(1, len(table.attributes))
    weight = 1 / (max(1, table.rowsPerBlock) * attributeCount)

    return {
        table.name.lower() + "." + attribute.name.lower(): AttributeInfo(
            table.name,
            attribute.name,
            weight
        )
        for attribute in table.attributes
    }


def estimateAttributeRowSize(
    relationInfo: RelationInfo,
    attributeKeys: set[str]
) -> float:
    """Sabira tezine atributa koji ostaju u redu."""
    if len(attributeKeys) == 0:
        return relationInfo.rowSize

    if all(
        key in relationInfo.attributeInfo
        for key in attributeKeys
    ):
        return sum(
            relationInfo.attributeInfo[key].estimatedWeight
            for key in attributeKeys
        )

    # Kompatibilnost sa rucno napravljenim starim RelationInfo objektima.
    return (
        relationInfo.rowSize
        * len(attributeKeys)
        / max(1, relationInfo.attributeCount)
    )


def estimateRowsPerBlockFromRowSize(rowSize: float) -> int:
    return max(1, math.floor(1 / max(0.000001, rowSize)))


def getDistinctValue(catalog: Catalog, query: Query,
                     relationInfo: RelationInfo | None,
                     columnText: str) -> int:
    table, attribute = resolveAttribute(
        catalog,
        query,
        columnText
    )
    key = getAttributeKey(table, attribute)

    if relationInfo is not None and key in relationInfo.distinctValues:
        return max(1, relationInfo.distinctValues[key])

    return max(1, attribute.distinctValues)


def estimateComparisonSelectivity(
    catalog: Catalog,
    query: Query,
    expression: ComparisonExpression,
    relationInfo: RelationInfo | None = None
) -> float:
    """Procenjuje jedno poređenje bez histograma."""
    leftTable, leftAttribute = resolveAttribute(
        catalog,
        query,
        expression.left.text
    )
    leftDistinct = getDistinctValue(
        catalog,
        query,
        relationInfo,
        expression.left.text
    )

    if isinstance(expression.right, Literal):
        if leftAttribute.unique:
            equalitySelectivity = 1 / max(1, leftTable.rowCount)
        else:
            equalitySelectivity = 1 / leftDistinct
    else:
        rightDistinct = getDistinctValue(
            catalog,
            query,
            relationInfo,
            expression.right.text
        )
        equalitySelectivity = 1 / max(
            leftDistinct,
            rightDistinct
        )

    if expression.operator == "=":
        return clamp(equalitySelectivity)

    if expression.operator in ["!=", "<>"]:
        return clamp(1 - equalitySelectivity)

    return 0.5


def estimateExpressionSelectivity(
    catalog: Catalog,
    query: Query,
    expression: LogicalExpression | None,
    relationInfo: RelationInfo | None = None
) -> float:
    """Računa selektivnost stabla sa AND i OR operatorima."""
    if expression is None:
        return 1

    if isinstance(expression, ComparisonExpression):
        return estimateComparisonSelectivity(
            catalog,
            query,
            expression,
            relationInfo
        )

    first = estimateExpressionSelectivity(
        catalog,
        query,
        expression.left,
        relationInfo
    )
    second = estimateExpressionSelectivity(
        catalog,
        query,
        expression.right,
        relationInfo
    )

    if isinstance(expression, AndExpression):
        return clamp(first * second)

    return clamp(first + second - first * second)


def estimateConditionSelectivity(catalog: Catalog, query: Query,
                                 condition) -> float:
    """Kompatibilni interfejs za staru Condition klasu."""
    from sql_parser import parseWhereExpression

    expression = parseWhereExpression(
        condition.leftOperand + " " + condition.operator
        + " " + condition.rightOperand
    )
    return estimateExpressionSelectivity(
        catalog,
        query,
        expression
    )


def estimateSelectionRows(catalog: Catalog, query: Query,
                          table: TableStats) -> int:
    expression = getLocalExpression(catalog, query, table)
    selectivity = estimateExpressionSelectivity(
        catalog,
        query,
        expression
    )

    if table.rowCount == 0:
        return 0

    return max(1, math.ceil(table.rowCount * selectivity))


def createSelectionRelationInfo(catalog: Catalog, query: Query,
                                table: TableStats) -> RelationInfo:
    """Pravi statistiku tabele posle potisnute selekcije."""
    rows = estimateSelectionRows(catalog, query, table)
    localExpression = getLocalExpression(catalog, query, table)
    blocks = (
        table.blockCount
        if localExpression is None
        else estimateResultBlocks(rows, table.rowsPerBlock)
    )
    distinctValues = {}

    for attribute in table.attributes:
        key = getAttributeKey(table, attribute)
        distinctValues[key] = (
            0
            if rows == 0
            else min(rows, max(1, attribute.distinctValues))
        )

    availableAttributes = set(distinctValues)
    uniqueKeys = {
        getAttributeKey(table, attribute)
        for attribute in table.attributes
        if attribute.unique
    }
    attributeInfo = createAttributeInfo(table)

    return RelationInfo(
        [table.name],
        rows,
        blocks,
        table.rowsPerBlock,
        1 / max(1, table.rowsPerBlock),
        distinctValues,
        len(table.attributes),
        [],
        availableAttributes,
        uniqueKeys,
        True,
        1 / max(1, table.rowsPerBlock),
        attributeInfo
    )


def createBaseRelationInfo(table: TableStats) -> RelationInfo:
    """Pravi statistiku cele tabele bez lokalne selekcije."""
    distinctValues = {
        table.name.lower() + "." + attribute.name.lower(): min(
            attribute.distinctValues,
            table.rowCount
        )
        for attribute in table.attributes
    }
    availableAttributes = set(distinctValues)
    uniqueKeys = {
        table.name.lower() + "." + attribute.name.lower()
        for attribute in table.attributes
        if attribute.unique
    }
    attributeInfo = createAttributeInfo(table)
    return RelationInfo(
        [table.name],
        table.rowCount,
        table.blockCount,
        table.rowsPerBlock,
        1 / max(1, table.rowsPerBlock),
        distinctValues,
        len(table.attributes),
        [],
        availableAttributes,
        uniqueKeys,
        True,
        1 / max(1, table.rowsPerBlock),
        attributeInfo
    )


def estimateJoinInfo(catalog: Catalog, query: Query,
                     leftInfo: RelationInfo,
                     rightInfo: RelationInfo,
                     joinConditions
                     ) -> RelationInfo:
    """Pravi statistiku rezultata spajanja ili proizvoda."""
    if isinstance(joinConditions, list):
        joinExpression = (
            None
            if len(joinConditions) == 0
            else parseWhereExpression(
                " AND ".join(
                    condition.leftOperand + " " + condition.operator
                    + " " + condition.rightOperand
                    for condition in joinConditions
                )
            )
        )
    else:
        joinExpression = joinConditions
    combinedDistinct = dict(leftInfo.distinctValues)
    combinedDistinct.update(rightInfo.distinctValues)
    combinedAttributeInfo = dict(leftInfo.attributeInfo)
    combinedAttributeInfo.update(rightInfo.attributeInfo)
    combinedAttributes = set(combinedDistinct)

    if all(
        key in combinedAttributeInfo
        for key in combinedAttributes
    ):
        combinedRowSize = sum(
            combinedAttributeInfo[key].estimatedWeight
            for key in combinedAttributes
        )
    else:
        combinedRowSize = (
            (leftInfo.rowSize or leftInfo.rowBlockFraction)
            + (rightInfo.rowSize or rightInfo.rowBlockFraction)
        )
    temporaryInfo = RelationInfo(
        leftInfo.tableNames + rightInfo.tableNames,
        leftInfo.rowCount * rightInfo.rowCount,
        0,
        1,
        combinedRowSize,
        combinedDistinct,
        leftInfo.attributeCount + rightInfo.attributeCount,
        [],
        set(combinedDistinct),
        leftInfo.uniqueKeys | rightInfo.uniqueKeys,
        True,
        combinedRowSize,
        combinedAttributeInfo
    )
    selectivity = estimateExpressionSelectivity(
        catalog,
        query,
        joinExpression,
        temporaryInfo
    )
    rows = math.ceil(
        leftInfo.rowCount * rightInfo.rowCount * selectivity
    )

    if leftInfo.rowCount == 0 or rightInfo.rowCount == 0:
        rows = 0

    rowsPerBlock = estimateRowsPerBlockFromRowSize(combinedRowSize)
    blocks = estimateResultBlocks(rows, rowsPerBlock)

    for key in combinedDistinct:
        combinedDistinct[key] = (
            0
            if rows == 0
            else min(combinedDistinct[key], rows)
        )

    if joinExpression is not None and not containsOr(joinExpression):
        for comparison in flattenComparisons(joinExpression):
            if (comparison.operator != "="
                    or isinstance(comparison.right, Literal)):
                continue

            leftTable, leftAttribute = resolveAttribute(
                catalog,
                query,
                comparison.left.text
            )
            rightTable, rightAttribute = resolveAttribute(
                catalog,
                query,
                comparison.right.text
            )
            leftKey = getAttributeKey(leftTable, leftAttribute)
            rightKey = getAttributeKey(rightTable, rightAttribute)
            newValue = min(
                combinedDistinct[leftKey],
                combinedDistinct[rightKey],
                max(1, rows)
            )
            combinedDistinct[leftKey] = newValue
            combinedDistinct[rightKey] = newValue

    uniqueKeys = {
        key for key, value in combinedDistinct.items()
        if rows > 0 and value == rows
    }
    return RelationInfo(
        leftInfo.tableNames + rightInfo.tableNames,
        rows,
        blocks,
        rowsPerBlock,
        1 / rowsPerBlock,
        combinedDistinct,
        leftInfo.attributeCount + rightInfo.attributeCount,
        [],
        set(combinedDistinct),
        uniqueKeys,
        True,
        combinedRowSize,
        combinedAttributeInfo
    )


def estimateSortCost(blockCount: int,
                     bufferBlocks: int) -> int:
    """Cena spoljnog sortiranja sa početnim delovima i merge prolazima."""
    if blockCount <= 0:
        return 0

    initialRuns = math.ceil(blockCount / max(1, bufferBlocks))

    if initialRuns <= 1:
        return 2 * blockCount

    mergeFanout = max(2, bufferBlocks - 1)
    mergePasses = math.ceil(
        math.log(initialRuns, mergeFanout)
    )
    return 2 * blockCount * (1 + mergePasses)


def estimateProjectedRowsPerBlock(
    relationInfo: RelationInfo,
    selectedAttributeCount: int
) -> int:
    if selectedAttributeCount <= 0:
        return relationInfo.rowsPerBlock

    ratio = relationInfo.attributeCount / selectedAttributeCount
    return max(1, math.floor(relationInfo.rowsPerBlock * ratio))


def estimateProjectionRowsPerBlock(
    relationInfo: RelationInfo,
    selectedCount: int
) -> int:
    """Stari naziv ostavljen zbog kompatibilnosti."""
    return estimateProjectedRowsPerBlock(
        relationInfo,
        selectedCount
    )
