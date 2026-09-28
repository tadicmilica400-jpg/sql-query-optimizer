import copy

from models import Catalog, Query, RelationInfo
from semantic_analyzer import (
    getAttributeKey,
    resolveAttribute
)
from sql_parser import flattenComparisons
from statistics import (
    estimateAttributeRowSize,
    estimateResultBlocks,
    estimateRowsPerBlockFromRowSize
)


def getQueryOutputAttributes(catalog: Catalog,
                             query: Query) -> set[str]:
    """Vraca atribute potrebne za konacan rezultat."""
    result = set()

    for item in query.selectItems:
        if item.text == "*":
            for tableName in query.tableNames:
                table = catalog.findTable(tableName)

                for attribute in table.attributes:
                    result.add(getAttributeKey(table, attribute))
        elif item.aggregate is not None:
            if item.aggregate.argument != "*":
                table, attribute = resolveAttribute(
                    catalog,
                    query,
                    item.aggregate.argument
                )
                result.add(getAttributeKey(table, attribute))
        else:
            table, attribute = resolveAttribute(
                catalog,
                query,
                item.text
            )
            result.add(getAttributeKey(table, attribute))

    if query.orderBy is not None:
        table, attribute = resolveAttribute(
            catalog,
            query,
            query.orderBy
        )
        result.add(getAttributeKey(table, attribute))

    return result


def getRequiredAttributes(catalog: Catalog, query: Query,
                          includedTableNames: list[str]) -> set[str]:
    """Odredjuje sta jos mora da ostane u medjurezultatu."""
    included = {name.lower() for name in includedTableNames}
    required = getQueryOutputAttributes(catalog, query)

    for comparison in flattenComparisons(query.whereExpression):
        leftTable, leftAttribute = resolveAttribute(
            catalog,
            query,
            comparison.left.text
        )
        references = [(leftTable, leftAttribute)]

        if hasattr(comparison.right, "text"):
            try:
                rightTable, rightAttribute = resolveAttribute(
                    catalog,
                    query,
                    comparison.right.text
                )
                references.append((rightTable, rightAttribute))
            except ValueError:
                pass

        referencedTables = {
            table.name.lower() for table, attribute in references
        }

        if referencedTables.issubset(included):
            continue

        for table, attribute in references:
            if table.name.lower() in included:
                required.add(getAttributeKey(table, attribute))

    return required


def projectRelationInfo(relationInfo: RelationInfo,
                        requiredAttributes: set[str]) -> RelationInfo:
    """Preracunava statistike posle bezbedne projekcije."""
    available = (
        relationInfo.availableAttributes.copy()
        if len(relationInfo.availableAttributes) > 0
        else set(relationInfo.distinctValues)
    )
    kept = available & requiredAttributes

    if len(kept) == 0 and len(available) > 0:
        kept = {sorted(available)[0]}

    if kept == available:
        return copy.deepcopy(relationInfo)

    newCount = max(1, len(kept))
    newRowSize = estimateAttributeRowSize(relationInfo, kept)
    rowsPerBlock = estimateRowsPerBlockFromRowSize(newRowSize)
    blocks = estimateResultBlocks(
        relationInfo.rowCount,
        rowsPerBlock
    )
    sortedBy = []

    for key in relationInfo.sortedBy:
        if key not in kept:
            break

        sortedBy.append(key)

    return RelationInfo(
        relationInfo.tableNames.copy(),
        relationInfo.rowCount,
        blocks,
        rowsPerBlock,
        1 / rowsPerBlock,
        {
            key: value
            for key, value in relationInfo.distinctValues.items()
            if key in kept
        },
        newCount,
        sortedBy,
        kept,
        relationInfo.uniqueKeys & kept,
        True,
        newRowSize,
        {
            key: value
            for key, value in relationInfo.attributeInfo.items()
            if key in kept
        }
    )


def applyFusedProjection(plan, requiredAttributes: set[str]):
    """Suzenje reda spaja sa upisom rezultata prethodne operacije."""
    projectedPlan = copy.deepcopy(plan)
    oldInfo = projectedPlan.relationInfo
    newInfo = projectRelationInfo(oldInfo, requiredAttributes)

    if newInfo.availableAttributes == oldInfo.availableAttributes:
        return projectedPlan

    difference = newInfo.blockCount - oldInfo.blockCount
    projectedPlan.totalCost += difference
    lastStep = projectedPlan.steps[-1]
    lastStep.estimatedBlocks = newInfo.blockCount
    lastStep.materializationCost += difference
    lastStep.cumulativeCost += difference
    lastStep.description += (
        " Tokom istog upisa zadrzani su samo atributi potrebni kasnije."
    )
    if lastStep.formula != "":
        lastStep.formula += (
            "; ukupno = cena operacije"
            + " + b(projektovanog rezultata)"
        )
        lastStep.numericFormula += (
            "; ukupno = " + str(lastStep.operationCost)
            + " + " + str(newInfo.blockCount)
        )
    else:
        lastStep.formula = (
            "cena operacije + b(projektovanog rezultata)"
        )
        lastStep.numericFormula = (
            str(lastStep.operationCost) + " + "
            + str(newInfo.blockCount)
        )
    projectedPlan.relationInfo = newInfo
    projectedPlan.logicalTransformations.append(
        "Potisnuta projekcija na "
        + ", ".join(sorted(newInfo.availableAttributes))
    )
    return projectedPlan
