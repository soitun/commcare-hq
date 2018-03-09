from __future__ import absolute_import
from corehq.motech.openmrs.logger import logger
from corehq.motech.openmrs.repeater_helpers import (
    CaseTriggerInfo,
    OpenmrsResponse,
    create_visit,
    get_patient,
    update_person_properties,
    rollback_person_properties,
    update_person_name,
    rollback_person_name,
    update_person_address,
    rollback_person_address,
    create_person_address,
    delete_person_address,
    update_person_attribute,
    create_person_attribute,
    delete_person_attribute,
)
from corehq.motech.openmrs.workflow import Task, WorkflowTask, execute_workflow
from dimagi.utils.parsing import string_to_utc_datetime


def send_openmrs_data(requests, form_json, openmrs_config, case_trigger_infos, form_question_values):
    """
    Updates an OpenMRS patient and (optionally) creates visits.

    This involves several requests to the `OpenMRS REST Web Services`_. If any of those requests fail, we want to
    roll back previous changes to avoid inconsistencies in OpenMRS. To do this we define a workflow of tasks we
    want to do. Each workflow task has a reverse rollback task. If a task fails, all previous tasks are rolled
    back in reverse order


    :return: A response-like object that can be used by Repeater.handle_response


    .. _OpenMRS REST Web Services: https://wiki.openmrs.org/display/docs/REST+Web+Services+API+For+Clients
    """
    workflow_queue = []
    for info in case_trigger_infos:
        workflow_queue.append(
            SyncOpenmrsPatientTask(requests, info, form_json, form_question_values, openmrs_config)
        )

    success, errors = execute_workflow(workflow_queue)
    if success:
        return OpenmrsResponse(200, 'OK')
    else:
        logger.error('Errors encountered sending OpenMRS data: %s', errors)
        # TODO: Do something more useful with errors, like OpenmrsResponse.content or something
        return OpenmrsResponse(400, 'Bad Request')


class SyncPersonAttributesTask(WorkflowTask):
    """
    This task does not make any changes itself. It creates subtasks for creating and updating OpenMRS person
    attributes.
    """

    def __init__(self, *args, **kwargs):
        super(SyncPersonAttributesTask, self).__init__(None, None, None, *args, **kwargs)

    def run(self, requests, info, openmrs_config, person_uuid, attributes):
        existing_person_attributes = {
            attribute['attributeType']['uuid']: (attribute['uuid'], attribute['value'])
            for attribute in attributes
        }
        for person_attribute_type, value_source in openmrs_config.case_config.person_attributes.items():
            value = value_source.get_value(info)
            if person_attribute_type in existing_person_attributes:
                attribute_uuid, existing_value = existing_person_attributes[person_attribute_type]
                if value != existing_value:
                    rollback_task = Task(
                        update_person_attribute,
                        requests, person_uuid, attribute_uuid, person_attribute_type, existing_value,
                    )
                    self._subtasks.append(
                        WorkflowTask(
                            update_person_attribute,
                            requests, person_uuid, attribute_uuid, person_attribute_type, value,
                            rollback_task=rollback_task,
                            pass_result_as=None,
                        )
                    )
            else:
                rollback_task = Task(
                    delete_person_attribute,
                    requests, person_uuid
                )
                self._subtasks.append(
                    WorkflowTask(
                        create_person_attribute,
                        requests, person_uuid, person_attribute_type, value,
                        rollback_task=rollback_task,
                        pass_result_as='attribute_uuid',
                    )
                )


def create_visits(requests, info, form_json, form_question_values, openmrs_config, person_uuid):
    provider_uuid = getattr(openmrs_config, 'openmrs_provider', None)
    info.form_question_values.update(form_question_values)
    for form_config in openmrs_config.form_configs:
        logger.debug('Send visit for form?', form_config, form_json)
        if form_config.xmlns == form_json['form']['@xmlns']:
            logger.debug('Yes')
            create_visit(
                requests,
                person_uuid=person_uuid,
                provider_uuid=provider_uuid,
                visit_datetime=string_to_utc_datetime(form_json['form']['meta']['timeEnd']),
                values_for_concept={obs.concept: [obs.value.get_value(info)]
                                    for obs in form_config.openmrs_observations
                                    if obs.value.get_value(info)},
                encounter_type=form_config.openmrs_encounter_type,
                openmrs_form=form_config.openmrs_form,
                visit_type=form_config.openmrs_visit_type,
                # location_uuid=,  # location of case owner (CHW) > location[meta][openmrs_uuid]
            )


class SyncOpenmrsPatientTask(WorkflowTask):

    def __init__(self, *args, **kwargs):
        super(SyncOpenmrsPatientTask, self).__init__(None, None, None, *args, **kwargs)

    def run(self, requests, info, form_json, form_question_values, openmrs_config):
        assert isinstance(info, CaseTriggerInfo)

        logger.debug('Fetching OpenMRS patient UUID with ', info)
        patient = get_patient(requests, info, openmrs_config)
        if patient is None:
            raise ValueError('CommCare patient was not found in OpenMRS')
        person_uuid = patient['person']['uuid']
        logger.debug('OpenMRS patient found: ', person_uuid)

        self._subtasks.extend([
            WorkflowTask(
                update_person_properties,
                requests, info, openmrs_config, person_uuid,
                rollback_task=Task(rollback_person_properties, requests, patient['person'], openmrs_config),
                pass_result_as=None,
            ),

            WorkflowTask(
                update_person_name,
                requests, info, openmrs_config, person_uuid, patient['person']['preferredName']['uuid'],
                rollback_task=Task(rollback_person_name, requests, patient['person'], openmrs_config),
                pass_result_as=None,
            ),
        ])

        if patient['person']['preferredAddress']:
            self._subtasks.append(
                WorkflowTask(
                    update_person_address,
                    requests, info, openmrs_config, person_uuid, patient['person']['preferredAddress']['uuid'],
                    rollback_task=Task(rollback_person_address, requests, patient['person'], openmrs_config),
                    pass_result_as=None,
                )
            )

        else:
            self._subtasks.append(
                WorkflowTask(
                    create_person_address,
                    requests, info, openmrs_config, person_uuid,
                    rollback_task=Task(delete_person_address, requests, patient['person'], openmrs_config),
                    pass_result_as='address_uuid',
                )
            )

        self._subtasks.extend([
            SyncPersonAttributesTask(requests, info, openmrs_config, person_uuid, patient['person']['attributes']),

            # TODO: Make WorkflowTasks for the following:
            # create_visits(requests, info, form_json, form_question_values, openmrs_config, person_uuid)
        ])
