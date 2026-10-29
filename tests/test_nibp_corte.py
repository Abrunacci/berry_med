"""Cortar una medición de presión para arrancar otra, en los dos transportes.

Con `start-nibp` el arranque va pegado al STOP que corta la medición anterior.
Por USB, el equipo contesta ese STOP con un resultado "terminal", y si eso se
tomara como el final de la medición nueva, la pararía apenas arrancó.
"""

import asyncio

from src.data_parser import BMDataParser
from src.pm6750_protocol import CMD_NIBP, build_command
from src.serial_manager import NIBP_GRACIA_SEG, PM6750USBReader
from tests.tramas import estado_nibp, nibp_params

STOP_NIBP = build_command(CMD_NIBP, 0x00)


class PuertoFalso:
    is_open = True

    def __init__(self):
        self.escrito = []

    def write(self, datos):
        self.escrito.append(bytes(datos))

    def flush(self):
        pass

    def reset_output_buffer(self):
        pass

    def reset_input_buffer(self):
        pass


def lector():
    parser = BMDataParser()
    usb = PM6750USBReader(parser=parser, port="COM3")
    usb.ser = PuertoFalso()
    return parser, usb


def test_el_eco_del_stop_no_corta_la_medicion_nueva(reloj):
    parser, usb = lector()
    usb.reset_state()             # corte de la medición anterior: STOP
    usb._start_nibp_sync()        # start-nibp, pegado
    usb.ser.escrito.clear()
    reloj.avanzar(0.3)
    parser.add_data(nibp_params(status=estado_nibp(0b0010), sys=0, dia=0))  # "detenido"
    assert usb.nibp_running is True
    assert STOP_NIBP not in usb.ser.escrito


def test_un_resultado_real_cierra_la_medicion(reloj):
    parser, usb = lector()
    usb._start_nibp_sync()
    usb.ser.escrito.clear()
    reloj.avanzar(NIBP_GRACIA_SEG + 30)
    parser.add_data(nibp_params(status=estado_nibp(0b0000), sys=120, dia=80))
    assert usb.nibp_running is False
    assert STOP_NIBP in usb.ser.escrito


def test_el_corte_por_usb_le_manda_stop_al_equipo(reloj):
    _, usb = lector()
    usb._start_nibp_sync()
    usb.ser.escrito.clear()
    usb.reset_state()
    assert usb.nibp_running is False
    assert usb.ser.escrito == [STOP_NIBP]


def test_por_bluetooth_stop_nibp_desinfla():
    from src.bluetooth_manager import BMPatientMonitor

    class Cliente:
        is_connected = True

        def __init__(self):
            self.escrito = []

        async def write_gatt_char(self, uuid, datos):
            self.escrito.append(bytes(datos))

    bt = BMPatientMonitor(BMDataParser(), status_callback=lambda _: None)
    bt.client = Cliente()
    asyncio.run(bt.stop_nibp())
    assert bt.client.escrito == [STOP_NIBP]


def test_por_bluetooth_sin_conexion_stop_nibp_no_hace_nada():
    from src.bluetooth_manager import BMPatientMonitor
    bt = BMPatientMonitor(BMDataParser(), status_callback=lambda _: None)
    asyncio.run(bt.stop_nibp())
