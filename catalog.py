import json
from typing import Any

from models import (
    AttributeStats,
    Catalog,
    DataType,
    IndexStats,
    IndexType,
    TableStats
)


def requireField(data: dict[str, Any], fieldName: str,
                 objectName: str) -> Any:
    """Vraća obavezno polje ili prijavljuje jasnu grešku."""
    if fieldName not in data:
        raise ValueError(
            objectName + " nema obavezno polje " + fieldName + "."
        )

    return data[fieldName]


def requireNonNegativeInt(value: Any, fieldName: str) -> int:
    """Proverava celobrojnu statistiku koja ne sme biti negativna."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(
            fieldName + " mora biti nenegativan ceo broj."
        )

    return value


def parseAttribute(attributeData: dict[str, Any],
                   tableName: str) -> AttributeStats:
    """Pravi i proverava statistiku jednog atributa."""
    name = requireField(attributeData, "name", "Atribut")
    typeText = requireField(attributeData, "type", "Atribut " + name)
    unique = requireField(attributeData, "unique", "Atribut " + name)
    distinctValues = requireNonNegativeInt(
        requireField(
            attributeData,
            "distinctValues",
            "Atribut " + name
        ),
        "distinctValues atributa " + tableName + "." + name
    )

    if not isinstance(name, str) or name.strip() == "":
        raise ValueError("Naziv atributa ne sme biti prazan.")

    try:
        dataType = DataType(str(typeText).upper())
    except ValueError:
        raise ValueError(
            "Atribut " + tableName + "." + name
            + " ima nepodržan tip " + str(typeText) + "."
        )

    if not isinstance(unique, bool):
        raise ValueError(
            "Polje unique atributa " + tableName + "." + name
            + " mora biti true ili false."
        )

    return AttributeStats(
        name,
        dataType,
        unique,
        distinctValues
    )


def parseIndex(indexData: dict[str, Any],
               table: TableStats) -> IndexStats:
    """Pravi indeks i proverava njegove atribute."""
    name = requireField(indexData, "name", "Indeks")
    attributes = requireField(
        indexData,
        "attributes",
        "Indeks " + str(name)
    )
    typeText = requireField(
        indexData,
        "type",
        "Indeks " + str(name)
    )
    clustered = requireField(
        indexData,
        "clustered",
        "Indeks " + str(name)
    )

    if not isinstance(name, str) or name.strip() == "":
        raise ValueError("Naziv indeksa ne sme biti prazan.")

    if not isinstance(attributes, list) or len(attributes) == 0:
        raise ValueError(
            "Indeks " + str(name)
            + " mora imati bar jedan atribut."
        )

    for attributeName in attributes:
        if not isinstance(attributeName, str):
            raise ValueError(
                "Atributi indeksa " + str(name)
                + " moraju biti nazivi."
            )

        if table.findAttribute(attributeName) is None:
            raise ValueError(
                "Indeks " + str(name) + " referencira nepostojeći "
                + "atribut " + str(attributeName) + "."
            )

    lowerAttributeNames = [
        attributeName.lower()
        for attributeName in attributes
    ]

    if len(lowerAttributeNames) != len(set(lowerAttributeNames)):
        raise ValueError(
            "Indeks " + str(name)
            + " ima ponovljen atribut."
        )

    try:
        indexType = IndexType(str(typeText).upper())
    except ValueError:
        raise ValueError(
            "Indeks " + str(name)
            + " ima nepodržan tip " + str(typeText) + "."
        )

    if not isinstance(clustered, bool):
        raise ValueError(
            "Polje clustered indeksa " + str(name)
            + " mora biti true ili false."
        )

    treeHeight = indexData.get("treeHeight")

    if indexType == IndexType.B_PLUS_TREE:
        if (not isinstance(treeHeight, int)
                or isinstance(treeHeight, bool)
                or treeHeight <= 0):
            raise ValueError(
                "B+ indeks " + str(name)
                + " mora imati pozitivnu visinu stabla."
            )

    return IndexStats(
        str(name),
        [str(value) for value in attributes],
        indexType,
        clustered,
        treeHeight
    )


def parseTable(tableData: dict[str, Any]) -> TableStats:
    """Pravi jednu tabelu sa atributima i indeksima."""
    name = requireField(tableData, "name", "Tabela")

    if not isinstance(name, str) or name.strip() == "":
        raise ValueError("Naziv tabele ne sme biti prazan.")

    rowCount = requireNonNegativeInt(
        requireField(tableData, "rowCount", "Tabela " + name),
        "rowCount tabele " + name
    )
    blockCount = requireNonNegativeInt(
        requireField(tableData, "blockCount", "Tabela " + name),
        "blockCount tabele " + name
    )
    rowsPerBlock = requireNonNegativeInt(
        requireField(tableData, "rowsPerBlock", "Tabela " + name),
        "rowsPerBlock tabele " + name
    )

    if rowCount > 0 and rowsPerBlock == 0:
        raise ValueError(
            "rowsPerBlock tabele " + name + " mora biti veći od nule."
        )

    if rowCount > 0 and blockCount == 0:
        raise ValueError(
            "Tabela " + name
            + " sa redovima mora zauzimati bar jedan blok."
        )

    if rowCount > blockCount * rowsPerBlock:
        raise ValueError(
            "Statistike redova i blokova tabele " + name
            + " nisu usklađene."
        )

    attributeDataList = requireField(
        tableData,
        "attributes",
        "Tabela " + name
    )

    if not isinstance(attributeDataList, list) \
            or len(attributeDataList) == 0:
        raise ValueError(
            "Tabela " + name + " mora imati bar jedan atribut."
        )

    attributes = [
        parseAttribute(attributeData, name)
        for attributeData in attributeDataList
    ]

    for attribute in attributes:
        if rowCount > 0 and attribute.distinctValues == 0:
            raise ValueError(
                "Atribut " + name + "." + attribute.name
                + " mora imati bar jednu različitu vrednost."
            )

        if attribute.distinctValues > rowCount:
            raise ValueError(
                "Atribut " + name + "." + attribute.name
                + " ne može imati više različitih vrednosti od redova."
            )

        if (attribute.unique
                and attribute.distinctValues != rowCount):
            raise ValueError(
                "Jedinstveni atribut " + name + "."
                + attribute.name
                + " mora imati distinctValues jednak rowCount."
            )
    lowerNames = [attribute.name.lower() for attribute in attributes]

    if len(lowerNames) != len(set(lowerNames)):
        raise ValueError(
            "Tabela " + name + " ima ponovljen naziv atributa."
        )

    table = TableStats(
        name,
        rowCount,
        blockCount,
        rowsPerBlock,
        attributes,
        []
    )
    indexDataList = tableData.get("indexes", [])

    if not isinstance(indexDataList, list):
        raise ValueError(
            "Polje indexes tabele " + name + " mora biti lista."
        )

    table.indexes = [
        parseIndex(indexData, table)
        for indexData in indexDataList
    ]
    lowerIndexNames = [index.name.lower() for index in table.indexes]

    if len(lowerIndexNames) != len(set(lowerIndexNames)):
        raise ValueError(
            "Tabela " + name + " ima ponovljen naziv indeksa."
        )

    return table


def loadCatalog(fileName: str) -> Catalog:
    """Učitava i proverava kompletan JSON katalog."""
    with open(fileName, "r", encoding="utf-8") as inputFile:
        data = json.load(inputFile)

    if not isinstance(data, dict):
        raise ValueError("Koreni JSON element mora biti objekat.")

    bufferBlocks = requireNonNegativeInt(
        requireField(data, "bufferBlocks", "JSON"),
        "bufferBlocks"
    )

    if bufferBlocks < 3:
        raise ValueError("Bafer mora imati najmanje 3 bloka.")

    schema = requireField(data, "schema", "JSON")

    if not isinstance(schema, dict):
        raise ValueError("Polje schema mora biti objekat.")

    tableDataList = requireField(schema, "tables", "Schema")

    if not isinstance(tableDataList, list) or len(tableDataList) == 0:
        raise ValueError("Schema mora imati bar jednu tabelu.")

    tables = [parseTable(tableData) for tableData in tableDataList]
    lowerNames = [table.name.lower() for table in tables]

    if len(lowerNames) != len(set(lowerNames)):
        raise ValueError("Katalog ima ponovljen naziv tabele.")

    return Catalog(bufferBlocks, tables)
