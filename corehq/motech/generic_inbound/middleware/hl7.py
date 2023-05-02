from gettext import gettext

import attr
from django.http import HttpResponse
from hl7apy import load_library
from hl7apy.core import Message
from hl7apy.exceptions import ChildNotFound, HL7apyException
from hl7apy.parser import parse_message

from corehq.motech.generic_inbound.exceptions import GenericInboundUserError
from corehq.motech.generic_inbound.middleware.base import BaseApiMiddleware
from corehq.motech.generic_inbound.utils import ApiResponse


@attr.s(kw_only=True, frozen=True, auto_attribs=True)
class Hl7ApiResponse(ApiResponse):
    hl7_response: str

    def _get_http_response(self):
        return HttpResponse(status=self.status, content=self.hl7_response)


class Hl7Middleware(BaseApiMiddleware):
    """API middleware for handling HL7 v2 payloads"""

    def __init__(self, request_data):
        super().__init__(request_data)
        self.hl7_message = None

    def _get_body_for_eval_context(self):
        try:
            self.hl7_message = parse_hl7(self.request_data.data)
            msh = self.hl7_message.msh
            return {
                "version": msh.version_id.value,
                "message_type": {
                    "code": msh.message_type.message_code.value,
                    "event": msh.message_type.trigger_event.value,
                    "structure": msh.message_type.message_structure.value,
                },
                "message": hl7_message_to_dict(self.hl7_message, False)
            }
        except HL7apyException as e:
            raise GenericInboundUserError(gettext("Error parsing HL7: {}").format(str(e)))

    def get_success_response(self, response_json):
        hl7_response = self._get_ack("success", 200)
        return Hl7ApiResponse(status=200, internal_response=response_json, hl7_response=hl7_response)

    def _get_generic_error(self, status_code, message):
        hl7_response = self._get_ack(message, status_code)
        return Hl7ApiResponse(status=status_code, internal_response={'error': message}, hl7_response=hl7_response)

    def _get_submission_error_response(self, status_code, form_id, message):
        hl7_response = self._get_ack(message, status_code)
        return Hl7ApiResponse(status=status_code, internal_response={
            'error': message,
            'form_id': form_id,
        }, hl7_response=hl7_response)

    def _get_validation_error(self, status_code, message, errors):
        hl7_response = self._get_ack(message, status_code, errors)
        return Hl7ApiResponse(status=status_code, internal_response={
            'error': message,
            'errors': errors,
        }, hl7_response=hl7_response)

    def _get_ack(self, ack_text, status_code, errors=None):
        ack = Message("ACK", version=self.hl7_message.msh.version_id.value)
        ack.msh.msh_5 = self.hl7_message.msh.msh_3  # receiving application
        ack.msh.msh_6 = self.hl7_message.msh.msh_4  # receiving facility
        ack.msh.msh_4 = self.hl7_message.msh.msh_6  # sending facility
        ack.msh.msh_11 = self.hl7_message.msh.msh_11  # processing ID
        ack.msh.msh_10 = self.request_data.request_id  # outbound control ID

        ack.msa.msa_1 = self._status_code_to_hl7_status(status_code)
        ack.msa.msa_2 = self.hl7_message.msh.msh_10  # inbound control ID
        ack.msa.msa_3 = ack_text

        if errors:
            for error in errors:
                err = ack.add_segment("ERR")
                err.err_3 = "207"  # application error
                err.err_4 = "E"  # severity: error
                err.err_8 = error["message"]
                err.err_9 = "HD"  # inform help desk
        return ack.to_er7()

    def _status_code_to_hl7_status(self, status_code):
        if 200 <= status_code < 300:
            return 'AA'
        if 400 <= status_code < 500:
            return 'AE'
        return 'AR'


def hl7_str_to_dict(raw_hl7: str, use_long_name: bool = True) -> dict:
    """Convert an HL7 string to a dictionary
    :param raw_hl7: The input HL7 string
    :param use_long_name: Whether to use the long names
                          (e.g. "patient_name" instead of "pid_5")
    :returns: A dictionary representation of the HL7 message
    """
    message = parse_hl7(raw_hl7)
    return hl7_message_to_dict(message, use_long_name)


def hl7_message_to_dict(message, use_long_name: bool = True) -> dict:
    """Convert an HL7 message to a dictionary
    :param message: An HL7 message
    :param use_long_name: Whether to use the long names
                          (e.g. "patient_name" instead of "pid_5")
    :returns: A dictionary representation of the HL7 message
    """
    lib = load_library(message.version)
    base_datatypes = lib.get_base_datatypes()
    return _hl7_message_to_dict(message, set(base_datatypes), use_long_name=use_long_name)


def parse_hl7(raw_hl7):
    raw_hl7 = raw_hl7.replace("\n", "\r")
    message = parse_message(raw_hl7, find_groups=False)
    return message


def _hl7_message_to_dict(message_part, base_datatypes, use_long_name: bool = True) -> dict:
    """Convert an HL7 message to a dictionary
    :param message_part: The HL7 message as returned by :func:`hl7apy.parser.parse_message`
    :param use_long_name: Whether to use the long names (e.g. "patient_name" instead of "pid_5")
    :returns: A dictionary representation of the HL7 message
    """
    if message_part.children:
        data = {}
        for child in message_part.children:
            name = str(child.name)
            if use_long_name:
                name = str(child.long_name).lower() if child.long_name else name

            try:
                data_type = getattr(child, "datatype")
            except ChildNotFound:
                data_type = None

            if data_type and data_type in base_datatypes:
                # don't nest basic data types
                dictified = child.value
                dictified = dictified.value if hasattr(dictified, 'value') else dictified
            else:
                dictified = _hl7_message_to_dict(child, use_long_name=use_long_name, base_datatypes=base_datatypes)

            if name in data:
                if not isinstance(data[name], list):
                    data[name] = [data[name]]
                data[name].append(dictified)
            else:
                data[name] = dictified
        return data
    else:
        return message_part.to_er7()
