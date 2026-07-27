import route_sidecar


def test_route_sidecar_exposes_only_health_and_route_endpoints():
    app = route_sidecar.build_app()
    resources = {resource.canonical for resource in app.router.resources()}
    assert resources == {"/healthz", "/api/route", "/api/route_day"}


def test_route_sidecar_does_not_import_polling_app():
    assert "app" not in route_sidecar.__dict__
