"""Kompatibilni ulaz u optimizator.

Stari testovi uvoze funkcije iz ovog fajla, pa su javna imena ostala
na istom mestu. Implementacija je podeljena po manjim modulima.
"""

from access_paths import (
    applyAccessPlanToInfo,
    conditionToText,
    copyRelationInfo,
    createAccessPlans,
    estimateIndexAccessCost,
    estimateLinearScanCost,
    estimateMaterializationCost,
    estimateRowsForConditions,
    estimateSelectionInfo,
    findAttributeConditions,
    findBestAccessPlan,
    findUsableIndexConditions,
    findUsableIndexes,
    getIndexSelectionAlgorithm,
    getPhysicalTableOrder
)
from catalog import loadCatalog
from join_planner import (
    addAccessStep,
    candidateSortKey,
    createBasePlans,
    createJoinCandidates,
    createPlanForOrder,
    estimateHashJoinCost,
    estimateIndexLookupCost,
    estimateRowsPerIndexLookup,
    findJoinIndexes,
    getConditionAttributeForTable,
    isSortedOn,
    planSortKey
)
from operators import (
    DifferenceOperator,
    IntersectionOperator,
    UnionOperator,
    addAggregateOperation,
    addFinalOperations,
    addProjectionOperation,
    addSortOperation,
    createAllPlans,
    createSetPlan,
    estimateSetRows,
    findBestPlan,
    findOptimizedPlan
)
from semantic_analyzer import (
    areTypesCompatible,
    combineAnd,
    containsOr,
    expressionToText,
    getAttributeKey,
    getConnectingConditions,
    getExpressionTables,
    getJoinConditions,
    getJoinExpression,
    getLocalConditions,
    getLocalExpression,
    getOutputTypes,
    isAttributeReference,
    isConstant,
    isQuotedValue,
    resolveAttribute,
    validateConstantType,
    validateQuery
)
from sql_parser import (
    comparisonToCondition,
    findKeyword,
    findTopLevelKeyword,
    flattenComparisons,
    normalizeQueryText,
    parseCondition,
    parseQuery,
    parseWhereExpression,
    splitOutsideQuotes,
    splitSetOperation,
    tokenizeWhere
)
from statistics import (
    clamp,
    createAttributeInfo,
    createBaseRelationInfo,
    createSelectionRelationInfo,
    estimateAttributeRowSize,
    estimateComparisonSelectivity,
    estimateConditionSelectivity,
    estimateExpressionSelectivity,
    estimateJoinInfo,
    estimateProjectionRowsPerBlock,
    estimateProjectedRowsPerBlock,
    estimateResultBlocks,
    estimateRowsPerBlockFromRowSize,
    estimateSelectionRows,
    estimateSortCost,
    getDistinctValue
)


def findBruteForceBestPlan(catalog, query, pushProjections=False):
    """Uvozi referentnu pretragu tek kada je potrebna."""
    from physical_search import findBruteForceBestPlan as findReference

    return findReference(catalog, query, pushProjections)


def generateDpPlans(catalog, query, pushProjections=False):
    from physical_search import generateDpPlans as generatePlans

    return generatePlans(catalog, query, pushProjections)


def generateBruteForcePlans(catalog, query, pushProjections=False):
    from physical_search import generateBruteForcePlans as generatePlans

    return generatePlans(catalog, query, pushProjections)
