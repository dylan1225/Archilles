"""Offline guarantees. Live tool calling must be checked after installing the SDK."""
import asyncio
import json
from pathlib import Path

import pytest

import diagnose
import main
import redesign_agent as agent


def case():
    data = json.loads((Path(__file__).parents[1] / 'static/examples/1.json').read_text())
    d = diagnose.Diagnosis.model_validate(data['diagnosis'])
    return d.model_copy(update={'specimen': d.specimen.model_copy(update={'width_mm': 12, 'dimensions_source': 'user_context'})})


def candidate(kind='thicken_section', diameter=16):
    return agent.Candidate.model_validate({
        'solution': {'title': 'Larger rod', 'description': 'Replace the rod with a larger diameter.',
                     'pros': ['Greater bending capacity'], 'cons': ['More weight'], 'effort': 'moderate',
                     'addresses_root_cause': False, 'geometry': None, 'proposed_material_id': None,
                     'visual': {'kind': kind, 'label': 'Thicken rod'},
                     'section_change': {'shape': 'round', 'current_diameter_mm': 12,
                        'proposed_diameter_mm': diameter, 'current_width_mm': None,
                        'proposed_width_mm': None, 'current_thickness_mm': None,
                        'proposed_thickness_mm': None, 'length_mm': 220, 'dimensions_source': 'user_context'}},
        'assumptions': ['Same material and length']})


def select(ids):
    return agent.Selection(selected_ids=ids, explanation='Compared both proposals.', test_plan=['Compare deflection under the same small load.'])


def test_reject_overweight_and_attach_real_calculation():
    b = agent.CandidateBook(case(), agent.Constraints(max_weight_increase_pct=30), lambda e: None)
    r = b.submit('geometry_material', candidate())
    assert r['status'] == 'rejected'
    assert r['solution']['section_computed']['weight_ratio'] == 1.78
    assert r['solution']['section_computed']['strength_ratio'] == 2.37
    assert any('30%' in v for v in r['violations'])


def test_both_specialists_and_rejected_ids_are_enforced():
    b = agent.CandidateBook(case(), agent.Constraints(max_weight_increase_pct=30), lambda e: None)
    bad = b.submit('geometry_material', candidate())
    with pytest.raises(agent.AgentError, match='Both specialists'):
        b.finish(select([bad['id']]))
    good = b.submit('minimal_change', candidate(diameter=13))
    with pytest.raises(agent.AgentError, match='violates'):
        b.finish(select([bad['id']]))
    d, report = b.finish(select([good['id']]))
    assert len(d.solutions) == 1
    assert len(report['candidates']) == 2
    for ids in [['invented'], [good['id'], good['id']]]:
        with pytest.raises(agent.AgentError, match='unknown or duplicate'):
            b.finish(select(ids))


def test_support_and_material_constraints():
    b = agent.CandidateBook(case(), agent.Constraints(allow_added_support=False), lambda e: None)
    assert b.submit('minimal_change', candidate('add_support'))['status'] == 'rejected'
    assert b.submit('geometry_material', candidate('change_material'))['status'] == 'rejected'


def test_unknown_weight_is_not_verified():
    c = candidate('add_support'); c.solution.section_change = None
    b = agent.CandidateBook(case(), agent.Constraints(max_weight_increase_pct=30), lambda e: None)
    r = b.submit('minimal_change', c)
    assert r['status'] == 'conditional'
    assert 'unverified' in r['unverified'][0]


def test_fillet_never_uses_arbitrary_notched_bar_table():
    c = candidate('add_fillet'); c.solution.section_change = None
    c.solution.geometry = diagnose._Geometry(current_radius_mm=0, proposed_radius_mm=3, section_depth_mm=4, dimensions_source='user_context')
    b = agent.CandidateBook(case(), agent.Constraints(), lambda e: None)
    r = b.submit('geometry_material', c)
    assert r['solution']['computed'] is None
    assert r['status'] == 'conditional'
    assert c.solution.geometry is not None  # no mutation of caller-owned data


def test_candidate_limit_and_case_isolation():
    a = agent.CandidateBook(case(), agent.Constraints(), lambda e: None)
    b = agent.CandidateBook(case(), agent.Constraints(), lambda e: None)
    a.submit('minimal_change', candidate()); a.submit('minimal_change', candidate())
    assert 'error' in a.submit('minimal_change', candidate())
    assert not b.records


def test_estimated_dimensions_cannot_be_declared_measured():
    d = case(); d.specimen.dimensions_source = 'estimated_from_image'
    b = agent.CandidateBook(d, agent.Constraints(), lambda e: None)
    r = b.submit('geometry_material', candidate())
    assert r['status'] == 'conditional'
    assert r['solution']['section_change']['dimensions_source'] == 'estimated_from_image'


def test_constraint_validation():
    with pytest.raises(ValueError):
        agent.Constraints(max_weight_increase_pct=-1)
    with pytest.raises(ValueError):
        agent.Constraints(max_weight_increase_pct=float('nan'))


def test_insufficient_image_skips_specialists(monkeypatch):
    d = case().model_copy(update={'insufficient_image': True, 'solutions': [], 'specimen': None})
    async def fail(*args):
        raise AssertionError('Should not solve an insufficient photo')
    monkeypatch.setattr(agent, 'solve', fail)
    result = asyncio.run(agent.workflow(diagnosis=d, model=object()))
    assert result['agent_report'] is None


def test_timeout_and_provider_error_are_sanitized(monkeypatch):
    async def slow(*args):
        await asyncio.sleep(1)
    monkeypatch.setattr(agent, 'solve', slow)
    monkeypatch.setattr(agent, 'TIMEOUT_S', .01)
    with pytest.raises(agent.AgentError, match='exceeded'):
        asyncio.run(agent.workflow(diagnosis=case(), model=object()))
    async def fail(*args):
        raise RuntimeError('SECRET_PROVIDER_RESPONSE')
    monkeypatch.setattr(agent, 'solve', fail)
    with pytest.raises(agent.AgentError) as error:
        asyncio.run(agent.workflow(diagnosis=case(), model=object()))
    assert 'SECRET' not in str(error.value)


def test_observation_payload_uses_valid_image_data_url():
    class Model:
        def with_structured_output(self, *args, **kwargs): return self
        async def ainvoke(self, messages):
            url = messages[1]['content'][1]['image_url']['url']
            assert url.startswith('data:image/jpeg;base64,/9j/')
            return agent.Observation(visual_evidence=['Visible crack'], insufficient_image=True,
                insufficient_reason='Face hidden', material='wood', primary_mode='undetermined',
                confidence='low', probable_cause='Unknown', summary='Need another photo', specimen=None, alternative_modes=[])
    photo = (Path(__file__).parents[1] / 'static/examples/1.jpg').read_bytes()
    result = asyncio.run(agent.extract(photo, '', Model(), lambda e: None))
    assert result.insufficient_image


def test_stream_emits_progress_and_result(monkeypatch):
    async def fake(**kwargs):
        kwargs['emit']({'type': 'stage', 'message': 'Actual test stage'})
        await asyncio.sleep(0)
        return {'diagnosis': case().model_dump(mode='json'), 'meta': {}, 'agent_report': None}
    monkeypatch.setattr(agent, 'workflow', fake)
    async def collect():
        response = main.agent_stream(diagnosis=case())
        return [json.loads(line) async for line in response.body_iterator]
    events = asyncio.run(collect())
    assert [e['type'] for e in events] == ['stage', 'result']


def test_optional_feature_unavailable_does_not_break_gemini(monkeypatch):
    monkeypatch.setattr(agent, 'status', lambda: {'available': False, 'message': 'Install dependencies'})
    response = main.post_redesign(main.RedesignRequest(diagnosis=case()))
    assert response.status_code == 503
    assert main.get_example(1).status_code == 200


def test_weight_limit_is_checked_before_rounding():
    b = agent.CandidateBook(case(), agent.Constraints(max_weight_increase_pct=30), lambda e: None)
    c = candidate(diameter=12 * (1.304 ** .5))
    r = b.submit('geometry_material', c)
    assert r['solution']['section_computed']['weight_ratio'] == 1.3
    assert r['status'] == 'rejected'


def test_combined_design_cannot_hide_support_requirement():
    c = candidate('add_rib'); c.requires_added_support = True
    b = agent.CandidateBook(case(), agent.Constraints(allow_added_support=False), lambda e: None)
    assert b.submit('geometry_material', c)['status'] == 'rejected'


# --- Failure-mode IDs from the image model (Nemotron) ----------------------------------

def observation(mode, insufficient=False):
    specimen = case().specimen
    return agent.Observation(
        visual_evidence=['Long splinter along the grain'], insufficient_image=insufficient,
        insufficient_reason='Fracture face not visible' if insufficient else None, material='wood',
        primary_mode=mode, confidence='medium', probable_cause='Bent past its limit.', summary='It snapped in bending.',
        specimen=None if insufficient else diagnose._Specimen.model_validate(specimen.model_dump()),
        alternative_modes=[])


def test_schema_sent_to_the_model_lists_the_allowed_mode_ids():
    enum = agent.Observation.model_json_schema()['properties']['primary_mode']['enum']

    assert set(enum) == {*diagnose.mode_ids(diagnose.load_knowledge_base()), 'other', 'undetermined'}


@pytest.mark.parametrize('answer, expected', [
    ('bending_overload_wood', 'bending_overload_wood'),
    ('Bending overload wood', 'bending_overload_wood'),     # case and spaces
    ('Bending overload', 'bending_overload_wood'),          # the mode's display name
    ('bending_overload', 'bending_overload_wood'),          # unique shortened ID
    ('other/undetermined', 'undetermined'),                 # Nemotron echoing the prompt
    ('overload', None),                                     # ambiguous: never guessed
    ('corrosion', None),                                    # not in the knowledge base
])
def test_near_miss_mode_answers_are_matched_or_refused(answer, expected):
    assert agent.normalise_mode(answer) == expected


def test_insufficient_photo_is_accepted_whatever_mode_was_named():
    result = observation('other/undetermined', insufficient=True).diagnosis()

    assert result.insufficient_image and result.primary_mode == 'undetermined'


def test_unknown_mode_error_names_the_value_the_model_returned():
    with pytest.raises(agent.AgentError, match="'corrosion'"):
        observation('corrosion').diagnosis()


def test_matched_mode_reaches_the_diagnosis():
    assert observation('Bending overload').diagnosis().primary_mode == 'bending_overload_wood'
