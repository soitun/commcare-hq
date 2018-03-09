from __future__ import absolute_import


class Task(object):
    """
    Tasks are instantiated with a function to run, and args and kwargs that must be passed to it.

    Instances of this class are used for rollback.

    If a task is not instantiated with a function, it must implement a run() method. It will be passed the args
    and kwargs that the class was instantiated with.
    """

    def __init__(self, func=None, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run_func(self):
        if self.func:
            return self.func(*self.args, **self.kwargs)
        else:
            return self.run(*self.args, **self.kwargs)

    def run(self, *args, **kwargs):
        raise NotImplementedError


class WorkflowTask(Task):
    """
    Extends rollback tasks. Workflow tasks can define subtasks, which will be prepended to the workflow queue
    after the task is run.

    If a workflow task is not instantiated with a rollback task, either it must implement its own
    get_rollback_task() method, or it will be assumed that the task does not cause a change of state (probably
    because its subtasks do).
    """

    def __init__(self, rollback_task=None, pass_result_as=None, func=None, *args, **kwargs):
        """
        Instantiate WorkflowTask

        :param rollback_task: A Task instance
        :param pass_result_as: A parameter name, to be passed as a kwarg to rollback_task
        :param func: The function this task must run
        :param args: Arguments to pass to func or self.run()
        :param kwargs: Keyword arguments to pass to func or self.run()
        """
        self.rollback_task = rollback_task
        self.pass_result_as = pass_result_as
        super(WorkflowTask, self).__init__(func, *args, **kwargs)
        self._subtasks = []

    def get_rollback_task(self):
        return self.rollback_task

    def get_subtasks(self):
        return self._subtasks


# TODO: Write wrappers


def execute_workflow(workflow_queue):
    """
    We use two lists to execute a workflow:

    1. The (given) workflow queue, where tasks are pulled off the front and run, until an error is encountered.
    2. A rollback stack, where each workflow task appends a reverse task to undo its action, to be run if the
       workflow fails.

    """
    success = True
    errors = []
    rollback_stack = []

    try:
        while workflow_queue:
            workflow_task = workflow_queue.pop(0)
            rollback_task = workflow_task.get_rollback_task()
            if rollback_task:
                rollback_stack.append(rollback_task)
            result = workflow_task.run_func()
            if workflow_task.pass_result_as and rollback_task:
                # Useful for rollback tasks that must delete something created by the workflow task
                rollback_task.kwargs.update({workflow_task.pass_result_as: result})
            for i, subtask in enumerate(workflow_task.get_subtasks()):
                workflow_queue.insert(i, subtask)

    except Exception as workflow_error:
        errors.append('Workflow failed: {}'.format(workflow_error))
        for rollback_task in reversed(rollback_stack):
            try:
                rollback_task.run_func()
            except Exception as rollback_error:
                errors.append('Rollback error: {}'.format(rollback_error))

    return success, errors
