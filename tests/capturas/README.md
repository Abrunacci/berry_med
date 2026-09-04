# Capturas del equipo

Streams crudos del Berry, uno por escenario. Los graba
`tools/capturar_escenarios.py` con el equipo enchufado (en Windows) y los
consume `tests/test_capturas.py`.

Van versionados a propósito: son el registro de qué manda el equipo de verdad
en cada estado, y grabarlos cuesta una sesión con el hardware en la mano. Sin
ellos los tests siguen corriendo — saltean solos — pero se pierde la mitad que
verifica nuestra lectura del manual contra el firmware real.

Qué escenarios hay y qué prueba cada uno: `tests/escenarios.py`.

    python tools\capturar_escenarios.py --puerto COM3            # todos
    python tools\capturar_escenarios.py --puerto COM3 --solo ocioso
