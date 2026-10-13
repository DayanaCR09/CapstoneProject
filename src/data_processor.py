from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

import pandas as pd

from exceptions import DataValidationError

MissingStrategy = Literal["drop", "fill", "ffill", "bfill"]


class DataProcessor:
    """Transform tabular data with pandas without mutating input dataframes."""

    @staticmethod
    def _require_columns(
        dataframe: pd.DataFrame,
        columns: Iterable[Any],
        *,
        parameter: str,
    ) -> None:
        missing = [column for column in columns if column not in dataframe.columns]
        if missing:
            raise DataValidationError(
                f"{parameter} contains unknown column(s): {missing}",
                field=parameter,
            )

    @staticmethod
    def handle_missing_values(
        dataframe: pd.DataFrame,
        *,
        strategy: MissingStrategy = "drop",
        fill_value: Any = None,
        subset: Sequence[Any] | None = None,
        how: Literal["any", "all"] = "any",
    ) -> pd.DataFrame:
        """Drop, fill, forward-fill, or backward-fill missing values."""
        if not isinstance(dataframe, pd.DataFrame):
            raise DataValidationError("dataframe must be a pandas DataFrame")
        if strategy not in {"drop", "fill", "ffill", "bfill"}:
            raise DataValidationError(
                f"Unsupported missing-value strategy: {strategy}",
                field="strategy",
            )
        if how not in {"any", "all"}:
            raise DataValidationError(
                "how must be either 'any' or 'all'",
                field="how",
            )
        if subset is not None:
            DataProcessor._require_columns(dataframe, subset, parameter="subset")

        if strategy == "drop":
            return dataframe.dropna(subset=subset, how=how).copy()
        if strategy == "fill":
            if fill_value is None:
                raise DataValidationError(
                    "fill_value is required when strategy is 'fill'",
                    field="fill_value",
                )
            return dataframe.fillna(fill_value).copy()
        if fill_value is not None:
            raise DataValidationError(
                "fill_value can only be used when strategy is 'fill'",
                field="fill_value",
            )
        return dataframe.ffill() if strategy == "ffill" else dataframe.bfill()

    @staticmethod
    def parse_dates(
        dataframe: pd.DataFrame,
        columns: str | Sequence[str],
        *,
        format: str | None = None,
        errors: Literal["raise", "coerce"] = "raise",
        utc: bool = False,
    ) -> pd.DataFrame:
        """Convert the selected columns to pandas datetime values."""
        if not isinstance(dataframe, pd.DataFrame):
            raise DataValidationError("dataframe must be a pandas DataFrame")
        if errors not in {"raise", "coerce"}:
            raise DataValidationError(
                "errors must be either 'raise' or 'coerce'",
                field="errors",
            )

        date_columns = [columns] if isinstance(columns, str) else list(columns)
        if not date_columns:
            raise DataValidationError(
                "At least one date column must be specified",
                field="columns",
            )
        DataProcessor._require_columns(dataframe, date_columns, parameter="columns")

        result = dataframe.copy()
        for column in date_columns:
            result[column] = pd.to_datetime(
                result[column],
                format=format,
                errors=errors,
                utc=utc,
            )
        return result

    @staticmethod
    def join(
        left: pd.DataFrame,
        right: pd.DataFrame,
        *,
        on: str | Sequence[str] | None = None,
        left_on: str | Sequence[str] | None = None,
        right_on: str | Sequence[str] | None = None,
        how: Literal["left", "right", "outer", "inner", "cross"] = "left",
        suffixes: tuple[str, str] = ("_x", "_y"),
        validate: str | None = None,
    ) -> pd.DataFrame:
        """Join two dataframes using pandas merge semantics."""
        if not isinstance(left, pd.DataFrame) or not isinstance(right, pd.DataFrame):
            raise DataValidationError("left and right must be pandas DataFrames")
        if how not in {"left", "right", "outer", "inner", "cross"}:
            raise DataValidationError(f"Unsupported join type: {how}", field="how")

        if on is not None:
            keys = [on] if isinstance(on, str) else list(on)
            DataProcessor._require_columns(left, keys, parameter="on")
            DataProcessor._require_columns(right, keys, parameter="on")
        else:
            left_keys = [left_on] if isinstance(left_on, str) else left_on
            right_keys = [right_on] if isinstance(right_on, str) else right_on
            if left_keys is not None:
                DataProcessor._require_columns(left, left_keys, parameter="left_on")
            if right_keys is not None:
                DataProcessor._require_columns(
                    right,
                    right_keys,
                    parameter="right_on",
                )

        try:
            return left.merge(
                right,
                on=on,
                left_on=left_on,
                right_on=right_on,
                how=how,
                suffixes=suffixes,
                validate=validate,
            )
        except (ValueError, pd.errors.MergeError) as exc:
            raise DataValidationError(f"Unable to join dataframes: {exc}") from exc

    @staticmethod
    def group_and_aggregate(
        dataframe: pd.DataFrame,
        *,
        by: str | Sequence[str],
        aggregations: Mapping[str, Any],
        dropna: bool = True,
        sort: bool = False,
    ) -> pd.DataFrame:
        """Group rows by one or more columns and apply pandas aggregations."""
        if not isinstance(dataframe, pd.DataFrame):
            raise DataValidationError("dataframe must be a pandas DataFrame")
        group_columns = [by] if isinstance(by, str) else list(by)
        if not group_columns:
            raise DataValidationError(
                "At least one grouping column must be specified",
                field="by",
            )
        DataProcessor._require_columns(dataframe, group_columns, parameter="by")
        if not isinstance(aggregations, Mapping) or not aggregations:
            raise DataValidationError(
                "aggregations must be a non-empty mapping",
                field="aggregations",
            )
        DataProcessor._require_columns(
            dataframe,
            aggregations.keys(),
            parameter="aggregations",
        )

        try:
            grouped = dataframe.groupby(
                group_columns,
                dropna=dropna,
                sort=sort,
                observed=False,
            )
            return grouped.agg(dict(aggregations)).reset_index()
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                f"Unable to aggregate dataframe: {exc}",
                field="aggregations",
            ) from exc
