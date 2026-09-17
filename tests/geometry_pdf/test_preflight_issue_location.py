from copy import deepcopy

from services.api.exporters.preflight import preflight_project, scene_issue_location
from services.api.geometry import new_scene


def test_zipper_blocks_first_back_text_and_returns_its_stable_navigation_target():
    scene = new_scene('three-side-seal', 160, 230)
    scene['pouch_features'] = {}
    scene['geometry_hash'] = None
    for face in scene['faces']:
        face['objects'] = []
    back = next(face for face in scene['faces'] if face['id'] == 'back')
    back['objects'] = [{
        'id': 'back-ingredients', 'type': 'text', 'face_id': 'back',
        'x_mm': 20, 'y_mm': 32, 'width_mm': 100, 'height_mm': 8,
        'text': '뒤쪽 원재료 표시', 'font_size_pt': 10,
    }]
    original = deepcopy(scene)
    report = preflight_project({'scene': scene})
    issue = next(issue for issue in report['issues'] if issue.get('field') == 'faces.back.objects.0')
    assert issue['scope'] == 'review' and issue['severity'] == 'error'
    assert issue['face_id'] == 'back' and issue['object_id'] == 'back-ingredients'
    assert not report['review_allowed']
    assert scene == original


def test_index_and_identifier_paths_resolve_without_inventing_missing_objects_or_faces():
    scene = {'faces': [
        {'id': 'front', 'objects': [{'id': 'front-title'}]},
        {'id': 'back', 'objects': [{'id': 'back-ingredients'}]},
    ]}
    expected = {'face_id': 'back', 'object_id': 'back-ingredients'}
    assert scene_issue_location('faces.back.objects.0', scene) == expected
    assert scene_issue_location('faces.1.objects.0.width_mm', scene) == expected
    assert scene_issue_location('faces.back.objects.back-ingredients.text', scene) == expected
    assert scene_issue_location('faces.back.objects.9', scene) == {'face_id': 'back'}
    for field in ('faces.missing.objects.0', 'profile.requirements.min_ppi', 'faces', None):
        assert scene_issue_location(field, scene) == {}
