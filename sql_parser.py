import re
from dataclasses import dataclass

from models import (
    AggregateExpression,
    AggregateFunction,
    AndExpression,
    ColumnReference,
    ComparisonExpression,
    Condition,
    Literal,
    LogicalExpression,
    OrExpression,
    Query,
    SelectItem,
    SetOperator
)


@dataclass
class Token:
    kind: str
    text: str


def normalizeQueryText(queryText: str) -> str:
    """Sređuje razmake van string literala."""
    result = []
    quote = None
    previousWasSpace = False
    position = 0

    while position < len(queryText):
        character = queryText[position]

        if quote is not None:
            result.append(character)

            if character == quote:
                if (position + 1 < len(queryText)
                        and queryText[position + 1] == quote):
                    result.append(queryText[position + 1])
                    position += 1
                else:
                    quote = None

            position += 1
            continue

        if character in ["'", '"']:
            quote = character
            result.append(character)
            previousWasSpace = False
        elif character.isspace():
            if not previousWasSpace:
                result.append(" ")
                previousWasSpace = True
        else:
            result.append(character)
            previousWasSpace = False

        position += 1

    if quote is not None:
        raise ValueError("Navodnici u upitu nisu zatvoreni.")

    return "".join(result).strip()


def isWordBoundary(text: str, position: int) -> bool:
    if position < 0 or position >= len(text):
        return True

    return not (text[position].isalnum() or text[position] == "_")


def findTopLevelKeyword(text: str, keyword: str,
                        startPosition: int = 0) -> int:
    """Traži ključnu reč van navodnika i zagrada."""
    upperText = text.upper()
    upperKeyword = keyword.upper()
    quote = None
    depth = 0
    position = startPosition

    while position < len(text):
        character = text[position]

        if quote is not None:
            if character == quote:
                if (position + 1 < len(text)
                        and text[position + 1] == quote):
                    position += 2
                    continue

                quote = None

            position += 1
            continue

        if character in ["'", '"']:
            quote = character
            position += 1
            continue

        if character == "(":
            depth += 1
            position += 1
            continue

        if character == ")":
            depth -= 1

            if depth < 0:
                raise ValueError("Zagrade u upitu nisu ispravno napisane.")

            position += 1
            continue

        if (depth == 0
                and position <= len(text) - len(keyword)
                and upperText.startswith(upperKeyword, position)
                and isWordBoundary(text, position - 1)
                and isWordBoundary(
                    text,
                    position + len(keyword)
                )):
            return position

        position += 1

    if depth != 0:
        raise ValueError("Zagrade u upitu nisu ispravno napisane.")

    return -1


def findKeyword(queryText: str, keyword: str,
                startPosition: int = 0) -> int:
    """Kompatibilni naziv za staru pomoćnu funkciju."""
    return findTopLevelKeyword(
        queryText,
        keyword.strip(),
        startPosition
    )


def splitOutsideQuotes(text: str, separator: str) -> list[str]:
    """Razdvaja listu van navodnika i zagrada."""
    parts = []
    start = 0
    quote = None
    depth = 0
    position = 0

    while position < len(text):
        character = text[position]

        if quote is not None:
            if character == quote:
                if (position + 1 < len(text)
                        and text[position + 1] == quote):
                    position += 2
                    continue

                quote = None

            position += 1
            continue

        if character in ["'", '"']:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif (depth == 0
                and text[position:position + len(separator)].upper()
                == separator.upper()):
            parts.append(text[start:position].strip())
            position += len(separator)
            start = position
            continue

        position += 1

    parts.append(text[start:].strip())
    return parts


def tokenizeWhere(whereText: str) -> list[Token]:
    """Pretvara WHERE izraz u mali skup tokena."""
    tokens = []
    position = 0
    identifierPattern = re.compile(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
    )
    numberPattern = re.compile(r"-?(?:\d+\.\d+|\d+|\.\d+)")

    while position < len(whereText):
        character = whereText[position]

        if character.isspace():
            position += 1
            continue

        if character in ["'", '"']:
            quote = character
            start = position
            position += 1

            while position < len(whereText):
                if whereText[position] == quote:
                    if (position + 1 < len(whereText)
                            and whereText[position + 1] == quote):
                        position += 2
                        continue

                    position += 1
                    break

                position += 1
            else:
                raise ValueError("Navodnici u WHERE izrazu nisu zatvoreni.")

            tokens.append(Token("LITERAL", whereText[start:position]))
            continue

        if character == "(":
            tokens.append(Token("LPAREN", character))
            position += 1
            continue

        if character == ")":
            tokens.append(Token("RPAREN", character))
            position += 1
            continue

        operator = None

        for candidate in ["<=", ">=", "!=", "<>", "=", "<", ">"]:
            if whereText.startswith(candidate, position):
                operator = candidate
                break

        if operator is not None:
            tokens.append(Token("OPERATOR", operator))
            position += len(operator)
            continue

        numberMatch = numberPattern.match(whereText, position)

        if numberMatch is not None:
            tokens.append(Token("LITERAL", numberMatch.group(0)))
            position = numberMatch.end()
            continue

        identifierMatch = identifierPattern.match(whereText, position)

        if identifierMatch is not None:
            value = identifierMatch.group(0)
            upperValue = value.upper()

            if upperValue in ["AND", "OR"]:
                tokens.append(Token(upperValue, upperValue))
            else:
                tokens.append(Token("COLUMN", value))

            position = identifierMatch.end()
            continue

        raise ValueError(
            "Nepoznat znak u WHERE izrazu: " + character
        )

    return tokens


class WhereParser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.position = 0

    def current(self) -> Token | None:
        if self.position >= len(self.tokens):
            return None

        return self.tokens[self.position]

    def accept(self, kind: str) -> Token | None:
        token = self.current()

        if token is not None and token.kind == kind:
            self.position += 1
            return token

        return None

    def expect(self, kind: str) -> Token:
        token = self.accept(kind)

        if token is None:
            raise ValueError("Neispravno napisan WHERE izraz.")

        return token

    def parse(self) -> LogicalExpression:
        """Parsira ceo izraz po prioritetu OR, AND, poređenje."""
        if len(self.tokens) == 0:
            raise ValueError("WHERE klauzula je prazna.")

        expression = self.parseOr()

        if self.current() is not None:
            raise ValueError(
                "Neočekivan deo WHERE izraza: "
                + self.current().text
            )

        return expression

    def parseOr(self) -> LogicalExpression:
        expression = self.parseAnd()

        while self.accept("OR") is not None:
            expression = OrExpression(
                expression,
                self.parseAnd()
            )

        return expression

    def parseAnd(self) -> LogicalExpression:
        expression = self.parsePrimary()

        while self.accept("AND") is not None:
            expression = AndExpression(
                expression,
                self.parsePrimary()
            )

        return expression

    def parsePrimary(self) -> LogicalExpression:
        if self.accept("LPAREN") is not None:
            expression = self.parseOr()
            self.expect("RPAREN")
            return expression

        return self.parseComparison()

    def parseComparison(self) -> ComparisonExpression:
        left = self.expect("COLUMN")
        operator = self.expect("OPERATOR")
        right = self.current()

        if right is None or right.kind not in ["COLUMN", "LITERAL"]:
            raise ValueError("Poređenje nema ispravan desni operand.")

        self.position += 1
        rightOperand = (
            ColumnReference(right.text)
            if right.kind == "COLUMN"
            else Literal(right.text)
        )
        return ComparisonExpression(
            ColumnReference(left.text),
            operator.text,
            rightOperand
        )


def parseWhereExpression(whereText: str) -> LogicalExpression:
    return WhereParser(tokenizeWhere(whereText)).parse()


def flattenComparisons(
    expression: LogicalExpression | None
) -> list[ComparisonExpression]:
    """Vraća atomska poređenja iz stabla izraza."""
    if expression is None:
        return []

    if isinstance(expression, ComparisonExpression):
        return [expression]

    return (
        flattenComparisons(expression.left)
        + flattenComparisons(expression.right)
    )


def comparisonToCondition(
    expression: ComparisonExpression
) -> Condition:
    return Condition(
        expression.left.text,
        expression.operator,
        expression.right.text
    )


def parseCondition(conditionText: str) -> Condition:
    """Stari interfejs za parsiranje jednog poređenja."""
    expression = parseWhereExpression(conditionText)

    if not isinstance(expression, ComparisonExpression):
        raise ValueError("Očekivano je jedno WHERE poređenje.")

    return comparisonToCondition(expression)


def splitSetOperation(text: str) -> tuple[str, SetOperator, str] | None:
    """Pronalazi prvu skupovnu operaciju na najvišem nivou."""
    found = []

    for operator in SetOperator:
        position = findTopLevelKeyword(text, operator.value)

        if position != -1:
            found.append((position, operator))

    if len(found) == 0:
        return None

    position, operator = min(found, key=lambda item: item[0])
    leftText = text[:position].strip()
    rightText = text[position + len(operator.value):].strip()

    if operator == SetOperator.UNION \
            and rightText.upper().startswith("ALL "):
        raise ValueError("UNION ALL nije podržan.")

    if leftText == "" or rightText == "":
        raise ValueError(
            operator.value + " mora imati upit sa obe strane."
        )

    return leftText, operator, rightText


def parseSelectItem(itemText: str) -> SelectItem:
    aggregateMatch = re.fullmatch(
        r"(?i)(COUNT|MIN|MAX|SUM|AVG)\s*\(\s*"
        r"(\*|[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\)",
        itemText
    )

    if aggregateMatch is not None:
        function = AggregateFunction(
            aggregateMatch.group(1).upper()
        )
        argument = aggregateMatch.group(2)
        aggregate = AggregateExpression(function, argument)
        return SelectItem(aggregate.text, aggregate=aggregate)

    if itemText == "*":
        return SelectItem("*", column=ColumnReference("*"))

    return SelectItem(
        itemText,
        column=ColumnReference(itemText)
    )


def parseSimpleQuery(text: str) -> Query:
    query = Query(raw=text)
    upperText = text.upper()

    if not upperText.startswith("SELECT "):
        raise ValueError("Upit mora početi SELECT klauzulom.")

    for keyword in ["GROUP BY", "HAVING", "JOIN"]:
        if findTopLevelKeyword(text, keyword) != -1:
            raise ValueError(
                "Ključna reč " + keyword + " nije podržana."
            )

    fromPosition = findTopLevelKeyword(text, "FROM")

    if fromPosition == -1:
        raise ValueError("Upit nema FROM klauzulu.")

    wherePosition = findTopLevelKeyword(
        text,
        "WHERE",
        fromPosition + 4
    )
    orderPosition = findTopLevelKeyword(
        text,
        "ORDER BY",
        fromPosition + 4
    )

    if (wherePosition != -1 and orderPosition != -1
            and orderPosition < wherePosition):
        raise ValueError("ORDER BY mora biti posle WHERE klauzule.")

    selectPart = text[len("SELECT "):fromPosition].strip()
    fromStart = fromPosition + len("FROM")
    clausePositions = [
        position
        for position in [wherePosition, orderPosition]
        if position != -1
    ]
    fromEnd = min(clausePositions) if clausePositions else len(text)
    fromPart = text[fromStart:fromEnd].strip()

    if selectPart == "":
        raise ValueError("SELECT lista je prazna.")

    if fromPart == "":
        raise ValueError("FROM lista je prazna.")

    selectTexts = splitOutsideQuotes(selectPart, ",")
    query.selectItems = [
        parseSelectItem(itemText)
        for itemText in selectTexts
    ]
    query.selectAttributes = [
        item.text
        for item in query.selectItems
    ]
    query.tableNames = splitOutsideQuotes(fromPart, ",")

    if "" in query.selectAttributes:
        raise ValueError("SELECT lista nije ispravno napisana.")

    if "" in query.tableNames:
        raise ValueError("FROM lista nije ispravno napisana.")

    if wherePosition != -1:
        whereStart = wherePosition + len("WHERE")
        whereEnd = orderPosition if orderPosition != -1 else len(text)
        wherePart = text[whereStart:whereEnd].strip()
        query.whereExpression = parseWhereExpression(wherePart)
        query.conditions = [
            comparisonToCondition(expression)
            for expression in flattenComparisons(
                query.whereExpression
            )
        ]

    if orderPosition != -1:
        orderStart = orderPosition + len("ORDER BY")
        orderPart = text[orderStart:].strip()

        if orderPart == "" or "," in orderPart:
            raise ValueError(
                "ORDER BY podržava tačno jedan atribut."
            )

        orderParts = orderPart.split()

        if len(orderParts) == 1:
            query.orderBy = orderParts[0]
        elif len(orderParts) == 2:
            query.orderBy = orderParts[0]
            query.orderDirection = orderParts[1].upper()
        else:
            raise ValueError("Neispravna ORDER BY klauzula.")

    return query


def parseQuery(queryText: str) -> Query:
    """Parsira običan ili skupovni SQL upit."""
    text = normalizeQueryText(queryText)

    if text.endswith(";"):
        text = text[:-1].strip()

    if text == "":
        raise ValueError("SQL upit je prazan.")

    setParts = splitSetOperation(text)

    if setParts is None:
        query = parseSimpleQuery(text)
        query.raw = queryText
        return query

    leftText, setOperator, rightText = setParts
    query = Query(raw=queryText)
    query.setOperator = setOperator
    query.leftQuery = parseQuery(leftText)
    query.rightQuery = parseQuery(rightText)
    return query
