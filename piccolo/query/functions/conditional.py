from __future__ import annotations

import decimal
import math
from typing import TYPE_CHECKING, Any, Optional, Union

from piccolo.custom_types import BasicTypes, Combinable
from piccolo.querystring import QueryString

if TYPE_CHECKING:
    from piccolo.columns import Column


class Coalesce(QueryString):
    def __init__(
        self,
        *args: Union[Column, QueryString, BasicTypes],
        alias: Optional[str] = None,
    ):
        """
        Returns the first non-null value.

        Here's an example to try in the playground::

            >>> await Album.select(Album.release_date)
            [
                {'release_date': datetime.date(2021, 1, 1)},
                {'release_date': datetime.date(2025, 1, 1)},
                {'release_date': datetime.date(2022, 2, 2)},
                {'release_date': None}
            ]

        One of the values is null - we can specify a fallback value::

            >>> from piccolo.functions.conditional import Coalesce
            >>> await Album.select(
            ...     Coalesce(Album.release_date, datetime.date(2050, 1, 1))
            ... )
            [
                {'release_date': datetime.date(2021, 1, 1)},
                {'release_date': datetime.date(2025, 1, 1)},
                {'release_date': datetime.date(2022, 2, 2)},
                {'release_date': datetime.date(2050, 1, 1)}
            ]

        Or us this abbreviated syntax::

            >>> await Album.select(
            ...     Album.release_date | datetime.date(2050, 1, 1)
            ... )
            [
                {'release_date': datetime.date(2021, 1, 1)},
                {'release_date': datetime.date(2025, 1, 1)},
                {'release_date': datetime.date(2022, 2, 2)},
                {'release_date': datetime.date(2050, 1, 1)}
            ]

        """
        if len(args) < 2:
            raise ValueError("At least two values must be passed in.")

        #######################################################################
        # Preserve the original alias from the column.

        from piccolo.columns import Column

        first_arg = args[0]

        if isinstance(first_arg, Column):
            alias = (
                alias
                or first_arg._alias
                or first_arg._meta.get_default_alias()
            )
        elif isinstance(first_arg, QueryString):
            alias = alias or first_arg._alias

        #######################################################################

        placeholders = ", ".join("{}" for _ in args)

        super().__init__(f"COALESCE({placeholders})", *args, alias=alias)


class NullIf(QueryString):
    def __init__(
        self,
        identifier: Union[Column, QueryString],
        value: Union[BasicTypes, QueryString],
        alias: Optional[str] = None,
    ):
        """
        Returns null if the value in the database equals ``value``.

        An example is where a ``Varchar`` or ``Text`` column contains a mix of
        empty strings and null. We might want to standardise the response so
        it's just null.

        For example::

            class Venue(Table):
                name = Varchar()
                address = Text(null=True)

            >>> await Venue.select(Venue.name, NullIf(Venue.address, ''))
            [{'name': 'Amazing venue', 'address': None}]

        """
        # Preserve the original alias from the column.

        from piccolo.columns import Column

        if isinstance(identifier, Column):
            alias = (
                alias
                or identifier._alias
                or identifier._meta.get_default_alias()
            )
        elif isinstance(identifier, QueryString):
            alias = alias or identifier._alias

        #######################################################################

        super().__init__("NULLIF({}, {})", identifier, value, alias=alias)


def get_case_value_string(
    value: Union[Column, QueryString, BasicTypes, None],
) -> tuple[str, list[Any]]:
    """
    Works out how a ``THEN`` / ``ELSE`` value should appear in the SQL.

    Most values are passed to the database as query parameters, but numbers,
    booleans and ``None`` are added to the query directly. This is because
    Postgres can't infer the type of a bare parameter inside a ``CASE``
    statement, so it assumes it's text, and the query then fails::

        CASE WHEN "band"."popularity" > $1 THEN $2 ELSE $3 END
        # asyncpg.exceptions.DataError: invalid input for query argument
        # $2: 1 (expected str, got int)

    There's no SQL injection risk, because we only do this once we know the
    value is a Python number or boolean.

    :returns:
        A template fragment (either the literal SQL, or a ``{}`` placeholder),
        along with any args which belong to it.

    """
    if value is None:
        return ("NULL", [])
    elif isinstance(value, bool):
        return ("true" if value else "false", [])
    elif isinstance(value, int):
        return (str(value), [])
    elif isinstance(value, float) and math.isfinite(value):
        return (repr(value), [])
    elif isinstance(value, decimal.Decimal) and value.is_finite():
        return (str(value), [])
    else:
        return ("{}", [value])


class When(QueryString):
    def __init__(
        self,
        condition: Union[Combinable, QueryString],
        then: Union[Column, QueryString, BasicTypes, None],
    ):
        """
        A single branch of a :class:`Case` statement - don't use it directly.

        :param condition:
            A where clause, for example ``Band.popularity > 1000``.
        :param then:
            The value to return if the condition is true.

        """
        from piccolo.columns.combination import Combination, Where, WhereRaw

        if isinstance(condition, (Where, WhereRaw, Combination)):
            condition = condition.querystring
        elif not isinstance(condition, QueryString):
            raise ValueError("The condition must be a where clause.")

        then_string, then_args = get_case_value_string(then)

        super().__init__(
            f"WHEN {{}} THEN {then_string}",
            condition,
            *then_args,
        )


class Case(QueryString):
    def __init__(
        self,
        *whens: When,
        default: Union[Column, QueryString, BasicTypes, None] = None,
        alias: Optional[str] = None,
    ):
        """
        A SQL ``CASE`` statement - it returns a different value depending on
        which condition is matched first. It's the SQL equivalent of an
        ``if / elif / else`` block.

        Here's an example to try in the playground::

            >>> from piccolo.query.functions import Case, When

            >>> await Band.select(
            ...     Band.name,
            ...     Case(
            ...         When(Band.popularity > 900, then='super popular'),
            ...         When(Band.popularity > 500, then='popular'),
            ...         default='not popular',
            ...         alias='popularity_label',
            ...     ),
            ... )
            [
                {'name': 'Pythonistas', 'popularity_label': 'super popular'},
                {'name': 'Rustaceans', 'popularity_label': 'not popular'},
                {'name': 'C-Sharps', 'popularity_label': 'popular'}
            ]

        It can also be used in a ``where`` clause, and to sort results::

            >>> await Band.select(Band.name).order_by(
            ...     Case(
            ...         When(Band.name == 'Rustaceans', then=1),
            ...         default=2,
            ...     )
            ... )

        :param whens:
            Each branch of the statement, which are evaluated in order.
        :param default:
            The value to return if none of the conditions match (``ELSE`` in
            SQL). If not specified, ``null`` is returned.
        :param alias:
            The name of the column in the response.

        """
        if not whens:
            raise ValueError("At least one `When` must be passed in.")

        if not all(isinstance(when, When) for when in whens):
            raise ValueError("Only `When` instances can be passed in.")

        template = " ".join("{}" for _ in whens)
        args: list[Any] = list(whens)

        if default is not None:
            default_string, default_args = get_case_value_string(default)
            template += f" ELSE {default_string}"
            args.extend(default_args)

        super().__init__(
            f"CASE {template} END",
            *args,
            alias=alias or "case",
        )
