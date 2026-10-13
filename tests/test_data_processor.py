import pandas as pd
import pytest

from data_processor import DataProcessor
from exceptions import DataValidationError


def test_handle_missing_values_with_drop_and_subset():
    dataframe = pd.DataFrame(
        {"title": ["One", None, "Three"], "year": [2001, 2002, None]}
    )

    result = DataProcessor.handle_missing_values(
        dataframe,
        strategy="drop",
        subset=["title"],
    )

    assert result["title"].tolist() == ["One", "Three"]
    assert len(dataframe) == 3


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("fill", [1, 0, 2]),
        ("ffill", [1, 1, 2]),
        ("bfill", [1, 2, 2]),
    ],
)
def test_handle_missing_values_fills_or_propagates(strategy, expected):
    dataframe = pd.DataFrame({"count": [1, None, 2]})

    if strategy == "fill":
        result = DataProcessor.handle_missing_values(
            dataframe,
            strategy=strategy,
            fill_value=0,
        )
    else:
        result = DataProcessor.handle_missing_values(dataframe, strategy=strategy)

    assert result["count"].tolist() == expected


def test_handle_missing_values_rejects_invalid_options():
    dataframe = pd.DataFrame({"value": [None]})

    with pytest.raises(DataValidationError, match="fill_value"):
        DataProcessor.handle_missing_values(dataframe, strategy="fill")
    with pytest.raises(DataValidationError, match="strategy"):
        DataProcessor.handle_missing_values(dataframe, strategy="replace")


def test_parse_dates_converts_columns_without_mutating_input():
    dataframe = pd.DataFrame(
        {
            "published": ["2024-01-02", "not-a-date"],
            "title": ["One", "Two"],
        }
    )

    result = DataProcessor.parse_dates(
        dataframe,
        "published",
        errors="coerce",
    )

    assert pd.api.types.is_datetime64_any_dtype(result["published"])
    assert pd.isna(result.loc[1, "published"])
    assert dataframe["published"].tolist() == ["2024-01-02", "not-a-date"]


def test_parse_dates_raises_for_invalid_date_or_column():
    dataframe = pd.DataFrame({"published": ["not-a-date"]})

    with pytest.raises(ValueError):
        DataProcessor.parse_dates(dataframe, "published")
    with pytest.raises(DataValidationError, match="unknown column"):
        DataProcessor.parse_dates(dataframe, "missing")


def test_join_dataframes_on_shared_key():
    books = pd.DataFrame({"isbn": ["1", "2"], "title": ["One", "Two"]})
    authors = pd.DataFrame({"isbn": ["1"], "author": ["Ada"]})

    result = DataProcessor.join(books, authors, on="isbn", how="left")

    assert result.loc[0, ["isbn", "title", "author"]].tolist() == [
        "1",
        "One",
        "Ada",
    ]
    assert result.loc[1, ["isbn", "title"]].tolist() == ["2", "Two"]
    assert pd.isna(result.loc[1, "author"])


def test_join_dataframes_validates_columns_and_cardinality():
    left = pd.DataFrame({"key": [1, 1]})
    right = pd.DataFrame({"key": [1]})

    with pytest.raises(DataValidationError, match="unknown column"):
        DataProcessor.join(left, right, on="missing")
    with pytest.raises(DataValidationError, match="Unable to join"):
        DataProcessor.join(left, right, on="key", validate="one_to_one")


def test_group_and_aggregate_counts_and_sums():
    dataframe = pd.DataFrame(
        {
            "author": ["Ada", "Ada", "Grace"],
            "year": [2020, 2021, 2020],
            "copies": [2, 3, 5],
        }
    )

    result = DataProcessor.group_and_aggregate(
        dataframe,
        by="author",
        aggregations={"copies": ["sum", "mean"], "year": "count"},
    )

    assert result.set_index("author").loc["Ada", ("copies", "sum")] == 5
    assert result.set_index("author").loc["Ada", ("copies", "mean")] == 2.5
    assert result.set_index("author").loc["Grace", ("year", "count")] == 1


def test_group_and_aggregate_validates_requested_columns():
    dataframe = pd.DataFrame({"author": ["Ada"], "copies": [2]})

    with pytest.raises(DataValidationError, match="unknown column"):
        DataProcessor.group_and_aggregate(
            dataframe,
            by="missing",
            aggregations={"copies": "sum"},
        )
