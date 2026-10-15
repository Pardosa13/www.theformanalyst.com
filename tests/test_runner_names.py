import pytest

from runner_names import FieldMatcher, clean_name, same_horse, to_tab


@pytest.mark.parametrize('raw, cleaned', [
    ("Milly's Clever Too", 'millys clever too'),
    ('Millys Clever Too', 'millys clever too'),
    ('Dash Of Sun (NZ)', 'dash of sun'),
    ('  Mr.  Bigglesworth ', 'mr bigglesworth'),
    ('Three Virgo’s', 'three virgos'),
    (None, ''),
])
def test_clean_name(raw, cleaned):
    assert clean_name(raw) == cleaned


def test_same_horse():
    assert same_horse('thunder shoc', 'thunder shock')
    assert not same_horse('alpha', 'beta')


def test_to_tab():
    assert to_tab('7') == 7 and to_tab(3.0) == 3 and to_tab('') is None and to_tab(0) is None


def test_field_matcher_tab_first_name_second():
    field = FieldMatcher([
        {'horse_id': 1, 'name': "Milly's Clever Too", 'tab': '1'},
        {'horse_id': 2, 'name': 'Beta', 'tab': 2},
        {'horse_id': 3, 'name': 'Gamma (NZ)', 'tab': None},
    ])
    assert field.match('Millys Clever Too', 1)[0]['horse_id'] == 1
    assert field.match('Millys Clever Too', 1)[1] == 'tab'
    assert field.match('Gamma', None) == (field.by_name['gamma'], 'name')
    # TAB points at a different horse: refuse it, fall back to the name.
    assert field.match('Beta', 1)[0]['horse_id'] == 2
    assert field.match('Nobody', 2) == (None, None)
    assert field.match('', 2)[0]['horse_id'] == 2  # feed with TAB only
