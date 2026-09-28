from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union


class DataType(str, Enum):
    INT = "INT"
    DOUBLE = "DOUBLE"
    STRING = "STRING"
    DATE = "DATE"


class IndexType(str, Enum):
    B_PLUS_TREE = "B_PLUS_TREE"
    HASH = "HASH"


class AggregateFunction(str, Enum):
    COUNT = "COUNT"
    MIN = "MIN"
    MAX = "MAX"
    SUM = "SUM"
    AVG = "AVG"


class SetOperator(str, Enum):
    UNION = "UNION"
    INTERSECT = "INTERSECT"
    EXCEPT = "EXCEPT"


@dataclass
class AttributeStats:
    name: str
    dataType: DataType
    unique: bool
    distinctValues: int


@dataclass
class IndexStats:
    name: str
    attributes: list[str]
    indexType: IndexType
    clustered: bool
    treeHeight: int | None = None


@dataclass
class TableStats:
    name: str
    rowCount: int
    blockCount: int
    rowsPerBlock: int
    attributes: list[AttributeStats]
    indexes: list[IndexStats]

    def findAttribute(self, attributeName: str) -> AttributeStats | None:
        """Pronalazi atribut bez obzira na velika i mala slova."""
        for attribute in self.attributes:
            if attribute.name.lower() == attributeName.lower():
                return attribute

        return None


@dataclass
class Catalog:
    bufferBlocks: int
    tables: list[TableStats]

    def findTable(self, tableName: str) -> TableStats | None:
        """Pronalazi tabelu bez obzira na velika i mala slova."""
        for table in self.tables:
            if table.name.lower() == tableName.lower():
                return table

        return None


@dataclass
class ColumnReference:
    text: str


@dataclass
class Literal:
    text: str


@dataclass
class ComparisonExpression:
    left: ColumnReference
    operator: str
    right: Union[ColumnReference, Literal]


@dataclass
class AndExpression:
    left: LogicalExpression
    right: LogicalExpression


@dataclass
class OrExpression:
    left: LogicalExpression
    right: LogicalExpression


LogicalExpression = Union[
    ComparisonExpression,
    AndExpression,
    OrExpression
]


@dataclass
class AggregateExpression:
    function: AggregateFunction
    argument: str

    @property
    def text(self) -> str:
        return self.function.value + "(" + self.argument + ")"


@dataclass
class SelectItem:
    text: str
    column: ColumnReference | None = None
    aggregate: AggregateExpression | None = None


# Ova klasa ostaje zbog kompatibilnosti sa starim testovima.
@dataclass
class Condition:
    leftOperand: str
    operator: str
    rightOperand: str


@dataclass
class Query:
    selectAttributes: list[str] = field(default_factory=list)
    tableNames: list[str] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    orderBy: str | None = None
    orderDirection: str = "ASC"
    raw: str = ""
    whereExpression: LogicalExpression | None = None
    selectItems: list[SelectItem] = field(default_factory=list)
    setOperator: SetOperator | None = None
    leftQuery: Query | None = None
    rightQuery: Query | None = None


@dataclass
class AccessPlan:
    tableName: str
    algorithm: str
    indexName: str | None
    estimatedRows: int
    estimatedBlocks: int
    accessCost: float
    materializationCost: float
    usedConditions: list[Condition]
    sortedBy: list[str] = field(default_factory=list)
    description: str = ""
    totalCost: float = field(init=False)

    def __post_init__(self) -> None:
        self.totalCost = self.accessCost + self.materializationCost


@dataclass
class AttributeInfo:
    originalTable: str
    originalAttribute: str
    estimatedWeight: float


@dataclass
class RelationInfo:
    tableNames: list[str]
    rowCount: int
    blockCount: int
    rowsPerBlock: int
    rowBlockFraction: float
    distinctValues: dict[str, int]
    attributeCount: int
    sortedBy: list[str] = field(default_factory=list)
    availableAttributes: set[str] = field(default_factory=set)
    uniqueKeys: set[str] = field(default_factory=set)
    materialized: bool = True
    rowSize: float = 0
    attributeInfo: dict[str, AttributeInfo] = field(default_factory=dict)


@dataclass
class PlanOperator:
    operation: str
    algorithm: str
    description: str
    estimatedRows: int
    estimatedBlocks: int
    operationCost: float
    materializationCost: float
    cumulativeCost: float
    inputRows: list[int] = field(default_factory=list)
    inputBlocks: list[int] = field(default_factory=list)
    formula: str = ""
    numericFormula: str = ""


@dataclass
class PlanStep(PlanOperator):
    pass


@dataclass
class EvaluationPlan:
    tableOrder: list[str]
    steps: list[PlanStep] = field(default_factory=list)
    totalCost: float = 0
    relationInfo: RelationInfo | None = None
    logicalTransformations: list[str] = field(default_factory=list)
    searchStatistics: dict[str, int] = field(default_factory=dict)


# Stara imena su ostavljena da postojeći importi nastave da rade.
Attribute = AttributeStats
Index = IndexStats
Table = TableStats
ParsedQuery = Query
RelationEstimate = RelationInfo
