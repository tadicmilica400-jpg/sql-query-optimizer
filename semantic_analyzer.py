import re

from models import (
    AggregateFunction,
    AndExpression,
    Catalog,
    ColumnReference,
    ComparisonExpression,
    Condition,
    DataType,
    Literal,
    LogicalExpression,
    OrExpression,
    Query,
    TableStats
)
from sql_parser import comparisonToCondition, flattenComparisons


def isAttributeReference(attributeReference: str) -> bool:
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    return re.fullmatch(
        identifier + r"(?:\." + identifier + r")?",
        attributeReference
    ) is not None


def isQuotedValue(value: str) -> bool:
    return (
        len(value) >= 2
        and (
            (value[0] == "'" and value[-1] == "'")
            or (value[0] == '"' and value[-1] == '"')
        )
    )


def isConstant(operand: str) -> bool:
    if isQuotedValue(operand):
        return True

    try:
        float(operand)
        return True
    except ValueError:
        return False


def resolveAttribute(catalog: Catalog, query: Query,
                     attributeReference: str):
    """Razrešava kvalifikovan ili nekvalifikovan atribut."""
    if not isAttributeReference(attributeReference):
        raise ValueError(
            "Neispravna referenca atributa: "
            + attributeReference
        )

    parts = attributeReference.split(".")

    if len(parts) == 2:
        tableName, attributeName = parts
        usedTable = next(
            (
                name for name in query.tableNames
                if name.lower() == tableName.lower()
            ),
            None
        )

        if usedTable is None:
            raise ValueError(
                "Tabela " + tableName
                + " nije navedena u FROM klauzuli."
            )

        table = catalog.findTable(tableName)

        if table is None:
            raise ValueError("Tabela " + tableName + " ne postoji.")

        attribute = table.findAttribute(attributeName)

        if attribute is None:
            raise ValueError(
                "Atribut " + attributeName
                + " ne postoji u tabeli " + tableName + "."
            )

        return table, attribute

    found = []

    for tableName in query.tableNames:
        table = catalog.findTable(tableName)

        if table is not None:
            attribute = table.findAttribute(attributeReference)

            if attribute is not None:
                found.append((table, attribute))

    if len(found) == 0:
        raise ValueError(
            "Atribut " + attributeReference + " ne postoji."
        )

    if len(found) > 1:
        raise ValueError(
            "Atribut " + attributeReference + " je dvosmislen."
        )

    return found[0]


def validateConstantType(attribute, value: str) -> None:
    """Proverava da literal odgovara tipu atributa."""
    if attribute.dataType == DataType.INT:
        try:
            int(value)
        except ValueError:
            raise ValueError(
                "Vrednost " + value + " nije tipa INT."
            )
    elif attribute.dataType == DataType.DOUBLE:
        try:
            float(value)
        except ValueError:
            raise ValueError(
                "Vrednost " + value + " nije tipa DOUBLE."
            )
    elif attribute.dataType in [DataType.STRING, DataType.DATE]:
        if not isQuotedValue(value):
            raise ValueError(
                "Vrednost " + value
                + " mora biti napisana pod navodnicima."
            )


def areTypesCompatible(firstAttribute, secondAttribute) -> bool:
    if firstAttribute.dataType == secondAttribute.dataType:
        return True

    numericTypes = [DataType.INT, DataType.DOUBLE]
    return (
        firstAttribute.dataType in numericTypes
        and secondAttribute.dataType in numericTypes
    )


def validateExpression(catalog: Catalog, query: Query,
                       expression: LogicalExpression) -> None:
    """Semantički proverava celo stablo WHERE izraza."""
    if isinstance(expression, (AndExpression, OrExpression)):
        validateExpression(catalog, query, expression.left)
        validateExpression(catalog, query, expression.right)
        return

    leftTable, leftAttribute = resolveAttribute(
        catalog,
        query,
        expression.left.text
    )

    if isinstance(expression.right, Literal):
        validateConstantType(leftAttribute, expression.right.text)
        return

    rightTable, rightAttribute = resolveAttribute(
        catalog,
        query,
        expression.right.text
    )

    if not areTypesCompatible(leftAttribute, rightAttribute):
        raise ValueError(
            "Atributi " + expression.left.text + " i "
            + expression.right.text
            + " nisu kompatibilnog tipa."
        )


def getSimpleOutputTypes(catalog: Catalog,
                         query: Query) -> list[DataType]:
    outputTypes = []

    for item in query.selectItems:
        if item.aggregate is not None:
            function = item.aggregate.function

            if function == AggregateFunction.COUNT:
                outputTypes.append(DataType.INT)
            else:
                table, attribute = resolveAttribute(
                    catalog,
                    query,
                    item.aggregate.argument
                )
                outputTypes.append(
                    DataType.DOUBLE
                    if function == AggregateFunction.AVG
                    else attribute.dataType
                )
        elif item.text == "*":
            for tableName in query.tableNames:
                table = catalog.findTable(tableName)
                outputTypes.extend(
                    attribute.dataType
                    for attribute in table.attributes
                )
        else:
            table, attribute = resolveAttribute(
                catalog,
                query,
                item.text
            )
            outputTypes.append(attribute.dataType)

    return outputTypes


def getOutputTypes(catalog: Catalog,
                   query: Query) -> list[DataType]:
    if query.setOperator is None:
        return getSimpleOutputTypes(catalog, query)

    return getOutputTypes(catalog, query.leftQuery)


def validateSelectList(catalog: Catalog, query: Query) -> None:
    aggregateItems = [
        item for item in query.selectItems
        if item.aggregate is not None
    ]
    ordinaryItems = [
        item for item in query.selectItems
        if item.aggregate is None
    ]

    if (any(item.text == "*" for item in ordinaryItems)
            and len(query.selectItems) != 1):
        raise ValueError(
            "SELECT * se ne može kombinovati sa drugim stavkama."
        )

    if len(aggregateItems) > 0 and len(ordinaryItems) > 0:
        raise ValueError(
            "Bez GROUP BY nije dozvoljeno mešati agregate i atribute."
        )

    numericTypes = [DataType.INT, DataType.DOUBLE]

    for item in query.selectItems:
        if item.aggregate is None:
            if item.text != "*":
                resolveAttribute(catalog, query, item.text)

            continue

        aggregate = item.aggregate

        if aggregate.argument == "*":
            if aggregate.function != AggregateFunction.COUNT:
                raise ValueError(
                    aggregate.function.value
                    + " ne podržava argument *."
                )

            continue

        table, attribute = resolveAttribute(
            catalog,
            query,
            aggregate.argument
        )

        if (aggregate.function in [
            AggregateFunction.SUM,
            AggregateFunction.AVG
        ] and attribute.dataType not in numericTypes):
            raise ValueError(
                aggregate.function.value
                + " zahteva numerički atribut."
            )


def validateSimpleQuery(catalog: Catalog, query: Query) -> None:
    if len(query.tableNames) == 0:
        raise ValueError("Upit mora imati bar jednu tabelu.")

    if len(query.tableNames) > 4:
        raise ValueError("Upit može imati najviše 4 tabele.")

    if len(query.conditions) > 6:
        raise ValueError(
            "WHERE klauzula može imati najviše 6 uslova."
        )

    usedNames = set()

    for tableName in query.tableNames:
        if re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            tableName
        ) is None:
            raise ValueError(
                "Aliasi tabela nisu podržani: " + tableName
            )

        table = catalog.findTable(tableName)

        if table is None:
            raise ValueError("Tabela " + tableName + " ne postoji.")

        lowerName = tableName.lower()

        if lowerName in usedNames:
            raise ValueError(
                "Tabela " + tableName + " je navedena više puta."
            )

        usedNames.add(lowerName)

    validateSelectList(catalog, query)

    if query.whereExpression is not None:
        validateExpression(catalog, query, query.whereExpression)

    if query.orderBy is not None:
        if any(
            item.aggregate is not None
            for item in query.selectItems
        ):
            raise ValueError(
                "ORDER BY nad agregatnim rezultatom nije podržan."
            )

        resolveAttribute(catalog, query, query.orderBy)

        if query.orderDirection not in ["ASC", "DESC"]:
            raise ValueError(
                "ORDER BY podržava samo ASC ili DESC."
            )


def validateQuery(catalog: Catalog, query: Query) -> None:
    """Proverava običan ili skupovni upit."""
    if query.setOperator is None:
        validateSimpleQuery(catalog, query)
        return

    validateQuery(catalog, query.leftQuery)
    validateQuery(catalog, query.rightQuery)
    leftTypes = getOutputTypes(catalog, query.leftQuery)
    rightTypes = getOutputTypes(catalog, query.rightQuery)

    if len(leftTypes) != len(rightTypes):
        raise ValueError(
            query.setOperator.value
            + " zahteva isti broj izlaznih kolona."
        )

    for firstType, secondType in zip(leftTypes, rightTypes):
        if firstType == secondType:
            continue

        numericTypes = [DataType.INT, DataType.DOUBLE]

        if (firstType in numericTypes
                and secondType in numericTypes):
            continue

        raise ValueError(
            query.setOperator.value
            + " ima nekompatibilne tipove izlaznih kolona."
        )


def getAttributeKey(table, attribute) -> str:
    return table.name.lower() + "." + attribute.name.lower()


def expressionToText(expression: LogicalExpression | None) -> str:
    if expression is None:
        return ""

    if isinstance(expression, ComparisonExpression):
        return (
            expression.left.text + " " + expression.operator
            + " " + expression.right.text
        )

    operator = " AND " if isinstance(expression, AndExpression) else " OR "
    return (
        "(" + expressionToText(expression.left) + operator
        + expressionToText(expression.right) + ")"
    )


def getExpressionTables(catalog: Catalog, query: Query,
                        expression: LogicalExpression) -> set[str]:
    if isinstance(expression, (AndExpression, OrExpression)):
        return (
            getExpressionTables(catalog, query, expression.left)
            | getExpressionTables(catalog, query, expression.right)
        )

    leftTable, leftAttribute = resolveAttribute(
        catalog,
        query,
        expression.left.text
    )
    names = {leftTable.name.lower()}

    if isinstance(expression.right, ColumnReference):
        rightTable, rightAttribute = resolveAttribute(
            catalog,
            query,
            expression.right.text
        )
        names.add(rightTable.name.lower())

    return names


def combineAnd(
    first: LogicalExpression | None,
    second: LogicalExpression | None
) -> LogicalExpression | None:
    if first is None:
        return second

    if second is None:
        return first

    return AndExpression(first, second)


def getLocalExpression(catalog: Catalog, query: Query,
                       table: TableStats) -> LogicalExpression | None:
    """Izdvaja deo WHERE izraza koji pripada samo jednoj tabeli."""
    expression = query.whereExpression

    if expression is None:
        return None

    tableName = table.name.lower()

    def visit(node: LogicalExpression):
        names = getExpressionTables(catalog, query, node)

        if names.issubset({tableName}):
            return node

        if isinstance(node, AndExpression):
            return combineAnd(visit(node.left), visit(node.right))

        return None

    return visit(expression)


def getJoinExpression(catalog: Catalog, query: Query,
                      leftTableNames: list[str],
                      rightTableName: str):
    """Izdvaja izraz koji prvi put povezuje novu tabelu sa planom."""
    expression = query.whereExpression

    if expression is None:
        return None

    leftNames = {name.lower() for name in leftTableNames}
    rightName = rightTableName.lower()
    available = leftNames | {rightName}

    def isConnecting(node: LogicalExpression) -> bool:
        names = getExpressionTables(catalog, query, node)
        return (
            names.issubset(available)
            and rightName in names
            and len(names & leftNames) > 0
        )

    def visit(node: LogicalExpression):
        if isinstance(node, OrExpression):
            return node if isConnecting(node) else None

        if isinstance(node, AndExpression):
            return combineAnd(visit(node.left), visit(node.right))

        return node if isConnecting(node) else None

    return visit(expression)


def containsOr(expression: LogicalExpression | None) -> bool:
    if expression is None:
        return False

    if isinstance(expression, OrExpression):
        return True

    if isinstance(expression, AndExpression):
        return containsOr(expression.left) or containsOr(expression.right)

    return False


def getLocalConditions(catalog: Catalog, query: Query,
                       table: TableStats) -> list[Condition]:
    expression = getLocalExpression(catalog, query, table)
    return [
        comparisonToCondition(comparison)
        for comparison in flattenComparisons(expression)
    ]


def getJoinConditions(catalog: Catalog,
                      query: Query) -> list[Condition]:
    result = []

    for comparison in flattenComparisons(query.whereExpression):
        if isinstance(comparison.right, Literal):
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

        if leftTable.name.lower() != rightTable.name.lower():
            result.append(comparisonToCondition(comparison))

    return result


def getConnectingConditions(catalog: Catalog, query: Query,
                            leftTableNames: list[str],
                            rightTableName: str) -> list[Condition]:
    expression = getJoinExpression(
        catalog,
        query,
        leftTableNames,
        rightTableName
    )
    return [
        comparisonToCondition(comparison)
        for comparison in flattenComparisons(expression)
    ]

