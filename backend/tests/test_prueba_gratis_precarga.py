"""Prueba gratis — el formulario llega completo desde el sitio web (decisión 077).

El sitio enterprisedataai.com manda al visitante a `/try?sitio=…&email=…`. Los campos se
completan, escapados, y el envío sigue pasando por el formulario con su sello de tiempo.
"""

from app.trial import routes


def test_try_completa_los_campos_que_llegan_del_sitio_web() -> None:
    page = routes.trial_form_en(sitio="tienda.com", email="ana@tienda.com").body.decode()
    assert 'value="tienda.com"' in page
    assert 'value="ana@tienda.com"' in page
    assert 'name="t"' in page  # el sello de tiempo sigue: el envío lo hace este formulario


def test_probar_completa_los_campos_en_castellano() -> None:
    page = routes.trial_form(lang="es", sitio="tienda.com", email="ana@tienda.com").body.decode()
    assert "Probalo con tu tienda" in page
    assert 'value="ana@tienda.com"' in page


def test_la_precarga_se_escapa_y_se_recorta() -> None:
    page = routes.trial_form_en(sitio='"><script>x</script>', email="a" * 500).body.decode()
    assert "<script>x</script>" not in page
    assert 'value="' + "a" * 200 + '"' in page
    assert "a" * 201 not in page


def test_sin_precarga_los_campos_quedan_vacios() -> None:
    page = routes.trial_form_en().body.decode()
    assert 'name="sitio"' in page
    assert 'value=""' in page
