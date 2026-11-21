from decimal import Decimal


import pytest


from revops_buddy.domain.service import (
    round_spoken_amount, spoken_amount, with_spoken_amounts,
)




@pytest.mark.parametrize('value, expected', [
    ('0', '0'), ('9999.99', '9999.99'), ('10000', '10000'),
    ('10499.99', '10000'), ('10500', '10000'), ('10500.01', '11000'),
    ('261500', '261000'), ('261501', '262000'), ('55000', '55000'),
    ('-261500', '-261000'), ('-9999.99', '-9999.99'),
])
def test_rounding_policy(value, expected):
    assert round_spoken_amount(value) == Decimal(expected)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_amounts_rejected(value):
    with pytest.raises(ValueError):
        round_spoken_amount(value)


@pytest.mark.parametrize('language, rounded, exact, unchanged', [
    ('en', 'about two hundred sixty-one thousand', 'two hundred sixty-one thousand five hundred', 'fifty-five thousand'),
    ('es', 'aproximadamente doscientos sesenta y un mil', 'doscientos sesenta y un mil quinientos', 'cincuenta y cinco mil'),
    ('it', 'circa duecentosessantunomila', 'duecentosessantunomilacinquecento', 'cinquantacinquemila'),
])
def test_localized_amounts(language, rounded, exact, unchanged):
    assert spoken_amount('261500', language) == rounded
    assert spoken_amount('261500', language, exact=True) == exact
    assert spoken_amount('55000', language) == unchanged


def test_nested_annotations_preserve_exact_values_and_nonmoney():
    data = {'total': {'amount': '261500', 'known_amount': '261500',
                      'weighted_amount': '12500', 'deal_count': 261500},
            'deals': [{'amount': None, 'close_date': '2026-11-28', 'probability': '0.555'}]}
    result = with_spoken_amounts(data)
    assert result['total']['spoken_amount']['en'] == 'about two hundred sixty-one thousand'
    assert result['total']['spoken_exact_amount']['es'] == 'doscientos sesenta y un mil quinientos'
    assert result['total']['spoken_weighted_amount']['it'] == 'circa dodicimila'
    assert result['total']['amount'] == data['total']['amount']
    assert result['total']['deal_count'] == 261500
    assert 'spoken_amount' not in result['deals'][0]
    assert result['deals'] == data['deals']
    assert 'spoken_amount' not in data['total']
    assert with_spoken_amounts(result) == result


