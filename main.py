import argparse
import json
import sys

from optimizer import (
    createAccessPlans,
    expressionToText,
    findOptimizedPlan,
    loadCatalog,
    parseQuery,
    validateQuery
)
from physical_search import generateAllTableOrderPlans


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# Formatira cenu bez nepotrebnih decimala
def formatCost(cost):
    if abs(cost - round(cost)) < 0.000001:
        return str(int(round(cost)))

    return str(round(cost, 2))


# Ucitava viselinijski SQL upit sa standardnog ulaza
def readInteractiveQuery():
    print(
        "Unesite SQL upit "
        "(zavrsite ga tacka-zarezom):"
    )
    queryLines = []

    while True:
        try:
            line = input("> " if len(queryLines) == 0 else "  ")
        except EOFError:
            break

        queryLines.append(line)

        if line.rstrip().endswith(";"):
            break

    return "\n".join(queryLines)


# Ucitava SQL upit iz argumenta, fajla ili sa ulaza
def readQuery(arguments):
    if arguments.query is not None:
        return arguments.query

    if arguments.queryFile is not None:
        with open(
            arguments.queryFile,
            "r",
            encoding="utf-8"
        ) as inputFile:
            return inputFile.read()

    return readInteractiveQuery()


# Ispisuje jednu stranu parsiranog upita
def printSimpleQuery(query, title):
    print("\n" + title)
    print("SELECT:", ", ".join(query.selectAttributes))
    print("FROM:", ", ".join(query.tableNames))

    if query.whereExpression is not None:
        print(
            "WHERE:",
            expressionToText(query.whereExpression)
        )
    else:
        print("WHERE: nema uslova")

    if query.orderBy is not None:
        print(
            "ORDER BY:",
            query.orderBy,
            query.orderDirection
        )


def printQueryPart(query, title):
    if query.setOperator is None:
        printSimpleQuery(query, title)
        return

    print("\n" + title)
    print("OPERACIJA:", query.setOperator.value)
    printQueryPart(query.leftQuery, "LEVI UPIT")
    printQueryPart(query.rightQuery, "DESNI UPIT")


# Ispisuje osnovne informacije o parsiranom upitu
def printQuery(query):
    title = (
        "PARSIRANI UPIT"
        if query.setOperator is None
        else "PARSIRANI SKUPOVNI UPIT"
    )
    printQueryPart(query, title)


def printSimpleAccessPlans(catalog, query):
    if query.setOperator is not None:
        print("\nLeva strana " + query.setOperator.value + ":")
        printSimpleAccessPlans(catalog, query.leftQuery)
        print("\nDesna strana " + query.setOperator.value + ":")
        printSimpleAccessPlans(catalog, query.rightQuery)
        return

    for tableName in query.tableNames:
        table = catalog.findTable(tableName)
        plans = createAccessPlans(catalog, query, table)
        plans.sort(
            key=lambda plan: (
                plan.totalCost,
                plan.algorithm
            )
        )

        print("\nTabela", table.name + ":")

        for plan in plans:
            text = (
                "  " + plan.algorithm
                + " | redova: " + str(plan.estimatedRows)
                + " | blokova: " + str(plan.estimatedBlocks)
                + " | pristup: " + formatCost(plan.accessCost)
                + " | materijalizacija: "
                + formatCost(plan.materializationCost)
                + " | ukupno: " + formatCost(plan.totalCost)
            )

            if plan.indexName is not None:
                text += " | indeks: " + plan.indexName

            print(text)

        print("  Izabran pristup:", plans[0].algorithm)


# Ispisuje pristupne alternative za svaku tabelu
def printAccessPlans(catalog, query):
    print("\nPRISTUPNI PLANOVI")

    if query.setOperator is None:
        printSimpleAccessPlans(catalog, query)
    else:
        print("\nLevi upit:")
        printSimpleAccessPlans(catalog, query.leftQuery)
        print("\nDesni upit:")
        printSimpleAccessPlans(catalog, query.rightQuery)


# Vraca strukturni redosled listova levog-dubokog plana
def extractTableOrder(plan):
    if not hasattr(plan, "tableOrder"):
        raise ValueError("Plan nema podatak o redosledu tabela.")

    return list(plan.tableOrder)


# Stabilan izbor fizickog plana kada su ukupne cene jednake
def physicalPlanDisplayKey(plan):
    stepKey = tuple(
        (
            step.operation.lower(),
            step.algorithm.lower(),
            step.description.lower()
        )
        for step in plan.steps
    )
    finalOrder = (
        tuple(plan.relationInfo.sortedBy)
        if plan.relationInfo is not None
        else ()
    )
    return stepKey, finalOrder


# Grupise fizicke planove samo po uredjenom redosledu tabela
def groupPlansByTableOrder(plans):
    bestPlans = {}

    for plan in plans:
        orderKey = tuple(extractTableOrder(plan))
        currentPlan = bestPlans.get(orderKey)

        if (currentPlan is None
                or (
                    plan.totalCost,
                    physicalPlanDisplayKey(plan)
                ) < (
                    currentPlan.totalCost,
                    physicalPlanDisplayKey(currentPlan)
                )):
            bestPlans[orderKey] = plan

    result = list(bestPlans.values())
    result.sort(key=lambda plan: (
        plan.totalCost,
        tuple(
            name.lower()
            for name in extractTableOrder(plan)
        ),
        tuple(extractTableOrder(plan))
    ))
    return result


# Za prikaz redosleda koristi kandidate pre DP dominacije
def getPlansForOrderDisplay(catalog, query, optimizedPlans):
    if query.setOperator is not None:
        return groupPlansByTableOrder(optimizedPlans)

    physicalCandidates = generateAllTableOrderPlans(
        catalog,
        query
    )
    return groupPlansByTableOrder(physicalCandidates)


# Ispisuje najbolju cenu svakog razlicitog redosleda tabela
def printAllPlanCosts(plans):
    groupedPlans = groupPlansByTableOrder(plans)
    print("\nCENE SVIH RAZLIČITIH REDOSLEDA TABELA")
    print("======================================")

    for position, plan in enumerate(groupedPlans, 1):
        print(
            str(position) + ".",
            " -> ".join(extractTableOrder(plan)),
            "| najbolja cena:",
            formatCost(plan.totalCost)
        )


# Ispisuje kompletan najjeftiniji plan
def printBestPlan(plan, explainAll=False,
                  showStatistics=False):
    print("\nNAJJEFTINIJI PLAN")
    print(
        "Redosled tabela:",
        " -> ".join(plan.tableOrder)
    )

    for position, step in enumerate(plan.steps, 1):
        print(
            "\n" + str(position) + ".",
            step.operation,
            "-", step.algorithm
        )
        print("   ", step.description)

        if len(step.inputRows) > 0:
            print(
                "    Ulazi:",
                ", ".join(
                    str(rows) + " redova / " + str(blocks) + " blokova"
                    for rows, blocks in zip(
                        step.inputRows,
                        step.inputBlocks
                    )
                )
            )
        print(
            "    Procena rezultata:",
            step.estimatedRows,
            "redova,",
            step.estimatedBlocks,
            "blokova"
        )
        print(
            "    Cena operacije:",
            formatCost(step.operationCost)
        )
        print(
            "    Cena materijalizacije:",
            formatCost(step.materializationCost)
        )
        print(
            "    Kumulativna cena:",
            formatCost(step.cumulativeCost)
        )

        if explainAll and step.formula != "":
            print("    Formula:", step.formula)
            print("    Zamena:", step.numericFormula)

    print(
        "\nKonacna procena:",
        plan.relationInfo.rowCount,
        "redova,",
        plan.relationInfo.blockCount,
        "blokova"
    )
    print(
        "UKUPNA CENA:",
        formatCost(plan.totalCost),
        "blok-transfera"
    )

    if explainAll:
        print(
            "Fizicko uredjenje:",
            ", ".join(plan.relationInfo.sortedBy)
            if len(plan.relationInfo.sortedBy) > 0
            else "neuredjeno"
        )
        print(
            "Dostupni atributi:",
            ", ".join(sorted(plan.relationInfo.availableAttributes))
        )
        if len(plan.relationInfo.attributeInfo) > 0:
            print("Procena sirine atributa:")

            for key in sorted(plan.relationInfo.availableAttributes):
                attributeInfo = plan.relationInfo.attributeInfo.get(key)

                if attributeInfo is not None:
                    print(
                        "  ", key, "=",
                        format(attributeInfo.estimatedWeight, ".6f"),
                        "bloka"
                    )

            print(
                "Ukupna relativna sirina reda:",
                format(plan.relationInfo.rowSize, ".6f"),
                "bloka"
            )
            print(
                "Redova po bloku posle projekcije:",
                plan.relationInfo.rowsPerBlock
            )
            print(
                "Procena blokova: ceil("
                + str(plan.relationInfo.rowCount) + " / "
                + str(plan.relationInfo.rowsPerBlock) + ") = "
                + str(plan.relationInfo.blockCount)
            )
        print(
            "Ukupna materijalizacija:",
            formatCost(sum(
                step.materializationCost
                for step in plan.steps
            )),
            "blokova"
        )
        print(
            "Broj placenih sortiranja:",
            sum(
                1 for step in plan.steps
                if step.operation == "Sortiranje"
                and step.operationCost > 0
            )
        )

        if len(plan.logicalTransformations) > 0:
            print("Logicke transformacije:")

            for transformation in plan.logicalTransformations:
                print("  -", transformation)

    if showStatistics and len(plan.searchStatistics) > 0:
        print("Statistika pretrage:")
        print(
            "  Generisano kandidata:",
            plan.searchStatistics.get("generated", 0)
        )
        print(
            "  Neprimenljivo:",
            plan.searchStatistics.get("inapplicable", 0)
        )
        print(
            "  Odbaceno dominacijom:",
            plan.searchStatistics.get("dominated", 0)
        )
        print(
            "  Sacuvano finalnih fizickih osobina:",
            plan.searchStatistics.get("kept", 0)
        )


# Formira argumente komandne linije
def createArgumentParser():
    argumentParser = argparse.ArgumentParser(
        description=(
            "Procena cene i izbor plana izvrsavanja SQL upita."
        )
    )
    argumentParser.add_argument(
        "schemaFile",
        nargs="?",
        default="primer_ulaza.json",
        help="JSON fajl sa semom i statistikama"
    )
    queryGroup = argumentParser.add_mutually_exclusive_group()
    queryGroup.add_argument(
        "--query",
        help="SQL upit prosledjen kao argument"
    )
    queryGroup.add_argument(
        "--query-file",
        dest="queryFile",
        help="Fajl koji sadrzi SQL upit"
    )
    argumentParser.add_argument(
        "--all-plans",
        action="store_true",
        help="Prikazuje najbolju cenu svakog razlicitog redosleda"
    )
    argumentParser.add_argument(
        "--explain-all",
        action="store_true",
        help="Prikazuje formule, fizicke osobine i transformacije"
    )
    argumentParser.add_argument(
        "--show-pruned",
        action="store_true",
        help="Prikazuje statistiku generisanih i odbacenih planova"
    )
    argumentParser.add_argument(
        "--show-statistics",
        action="store_true",
        help="Prikazuje detaljne procene plana"
    )
    return argumentParser


def main(commandLineArguments=None):
    argumentParser = createArgumentParser()
    arguments = argumentParser.parse_args(
        commandLineArguments
    )

    try:
        catalog = loadCatalog(arguments.schemaFile)
        queryText = readQuery(arguments)
        query = parseQuery(queryText)
        validateQuery(catalog, query)
        bestPlan, plans = findOptimizedPlan(catalog, query)

        printQuery(query)
        printAccessPlans(catalog, query)

        if arguments.all_plans:
            orderPlans = getPlansForOrderDisplay(
                catalog,
                query,
                plans
            )
            printAllPlanCosts(orderPlans)

        printBestPlan(
            bestPlan,
            arguments.explain_all or arguments.show_statistics,
            arguments.explain_all or arguments.show_pruned
        )
        return 0

    except (
        ValueError,
        FileNotFoundError,
        json.JSONDecodeError
    ) as error:
        print("Greska:", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
