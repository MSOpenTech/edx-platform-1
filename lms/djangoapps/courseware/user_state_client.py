"""
An implementation of :class:`XBlockUserStateClient`, which stores XBlock Scope.user_state
data in a Django ORM model.
"""

import itertools
from operator import attrgetter

try:
    import simplejson as json
except ImportError:
    import json

from xblock.fields import Scope, ScopeBase
from xblock_user_state.interface import XBlockUserStateClient
from courseware.models import StudentModule
from contracts import contract, new_contract
from opaque_keys.edx.keys import UsageKey

new_contract('UsageKey', UsageKey)


class DjangoXBlockUserStateClient(XBlockUserStateClient):
    """
    An interface that uses the Django ORM StudentModule as a backend.
    """

    class ServiceUnavailable(XBlockUserStateClient.ServiceUnavailable):
        """
        This error is raised if the service backing this client is currently unavailable.
        """
        pass

    class PermissionDenied(XBlockUserStateClient.PermissionDenied):
        """
        This error is raised if the caller is not allowed to access the requested data.
        """
        pass

    class DoesNotExist(XBlockUserStateClient.DoesNotExist):
        """
        This error is raised if the caller has requested data that does not exist.
        """
        pass

    def __init__(self, user):
        self.user = user

    @contract(
        username="basestring",
        block_key=UsageKey,
        scope=ScopeBase,
        fields="seq(basestring)|set(basestring)|None"
    )
    def get(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_key (UsageKey): The UsageKey identifying which xblock state to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Returns:
            dict: A dictionary mapping field names to values

        Raises:
            DoesNotExist if no entry is found.
        """
        assert self.user.username == username
        try:
            _usage_key, state = next(self.get_many(username, [block_key], scope, fields=fields))
        except StopIteration:
            raise self.DoesNotExist()

        return state

    @contract(username="basestring", block_key=UsageKey, state="dict(basestring: *)", scope=ScopeBase)
    def set(self, username, block_key, state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_key (UsageKey): The UsageKey identifying which xblock state to update.
            state (dict): A dictionary mapping field names to values
            scope (Scope): The scope to load data from
        """
        assert self.user.username == username
        self.set_many(username, {block_key: state}, scope)

    @contract(
        username="basestring",
        block_key=UsageKey,
        scope=ScopeBase,
        fields="seq(basestring)|set(basestring)|None"
    )
    def delete(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key (UsageKey): The UsageKey identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        assert self.user.username == username
        return self.delete_many(username, [block_key], scope, fields=fields)

    @contract(
        username="basestring",
        block_key=UsageKey,
        scope=ScopeBase,
        fields="seq(basestring)|set(basestring)|None"
    )
    def get_mod_date(self, username, block_key, scope=Scope.user_state, fields=None):
        """
        Get the last modification date for fields from the specified blocks.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key (UsageKey): The UsageKey identifying which xblock modification dates to retrieve.
            scope (Scope): The scope to retrieve from.
            fields: A list of fields to query. If None, delete all stored fields.
                Specific implementations are free to return the same modification date
                for all fields, if they don't store changes individually per field.
                Implementations may omit fields for which data has not been stored.

        Returns: list a dict of {field_name: modified_date} for each selected field.
        """
        results = self.get_mod_date_many(username, [block_key], scope, fields=fields)
        return {
            field: date for (_, field, date) in results
        }

    @contract(username="basestring", block_keys="seq(UsageKey)|set(UsageKey)")
    def _get_student_modules(self, username, block_keys):
        """
        Retrieve the :class:`~StudentModule`s for the supplied ``username`` and ``block_keys``.

        Arguments:
            username (str): The name of the user to load `StudentModule`s for.
            block_keys (list of :class:`~UsageKey`): The set of XBlocks to load data for.
        """
        course_key_func = attrgetter('course_key')
        by_course = itertools.groupby(
            sorted(block_keys, key=course_key_func),
            course_key_func,
        )

        for course_key, usage_keys in by_course:
            query = StudentModule.objects.chunked_filter(
                'module_state_key__in',
                usage_keys,
                student__username=username,
                course_id=course_key,
            )

            for student_module in query:
                usage_key = student_module.module_state_key.map_into_course(student_module.course_id)
                yield (student_module, usage_key)

    @contract(
        username="basestring",
        block_keys="seq(UsageKey)|set(UsageKey)",
        scope=ScopeBase,
        fields="seq(basestring)|set(basestring)|None"
    )
    def get_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Retrieve the stored XBlock state for a single xblock usage.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys ([UsageKey]): A list of UsageKeys identifying which xblock states to load.
            scope (Scope): The scope to load data from
            fields: A list of field values to retrieve. If None, retrieve all stored fields.

        Yields:
            (UsageKey, field_state) tuples for each specified UsageKey in block_keys.
            field_state is a dict mapping field names to values.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported, not {}".format(scope))

        modules = self._get_student_modules(username, block_keys)
        for module, usage_key in modules:
            if module.state is None:
                state = {}
            else:
                state = json.loads(module.state)
            yield (usage_key, state)

    @contract(username="basestring", block_keys_to_state="dict(UsageKey: dict(basestring: *))", scope=ScopeBase)
    def set_many(self, username, block_keys_to_state, scope=Scope.user_state):
        """
        Set fields for a particular XBlock.

        Arguments:
            username: The name of the user whose state should be retrieved
            block_keys_to_state (dict): A dict mapping UsageKeys to state dicts.
                Each state dict maps field names to values. These state dicts
                are overlaid over the stored state. To delete fields, use
                :meth:`delete` or :meth:`delete_many`.
            scope (Scope): The scope to load data from
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        # We do a find_or_create for every block (rather than re-using field objects
        # that were queried in get_many) so that if the score has
        # been changed by some other piece of the code, we don't overwrite
        # that score.
        for usage_key, state in block_keys_to_state.items():
            student_module, created = StudentModule.objects.get_or_create(
                student=self.user,
                course_id=usage_key.course_key,
                module_state_key=usage_key,
                defaults={
                    'state': json.dumps(state),
                    'module_type': usage_key.block_type,
                },
            )

            if not created:
                if student_module.state is None:
                    current_state = {}
                else:
                    current_state = json.loads(student_module.state)
                current_state.update(state)
                student_module.state = json.dumps(current_state)
                # We just read this object, so we know that we can do an update
                student_module.save(force_update=True)

    @contract(
        username="basestring",
        block_keys="seq(UsageKey)|set(UsageKey)",
        scope=ScopeBase,
        fields="seq(basestring)|set(basestring)|None"
    )
    def delete_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Delete the stored XBlock state for a many xblock usages.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key (UsageKey): The UsageKey identifying which xblock state to delete.
            scope (Scope): The scope to delete data from
            fields: A list of fields to delete. If None, delete all stored fields.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        student_modules = self._get_student_modules(username, block_keys)
        for student_module, _ in student_modules:
            if fields is None:
                student_module.state = "{}"
            else:
                current_state = json.loads(student_module.state)
                for field in fields:
                    if field in current_state:
                        del current_state[field]

                student_module.state = json.dumps(current_state)
            # We just read this object, so we know that we can do an update
            student_module.save(force_update=True)

    @contract(
        username="basestring",
        block_keys="seq(UsageKey)|set(UsageKey)",
        scope=ScopeBase,
        fields="seq(basestring)|set(basestring)|None"
    )
    def get_mod_date_many(self, username, block_keys, scope=Scope.user_state, fields=None):
        """
        Get the last modification date for fields from the specified blocks.

        Arguments:
            username: The name of the user whose state should be deleted
            block_key (UsageKey): The UsageKey identifying which xblock modification dates to retrieve.
            scope (Scope): The scope to retrieve from.
            fields: A list of fields to query. If None, delete all stored fields.
                Specific implementations are free to return the same modification date
                for all fields, if they don't store changes individually per field.
                Implementations may omit fields for which data has not been stored.

        Yields: tuples of (block, field_name, modified_date) for each selected field.
        """
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")

        student_modules = self._get_student_modules(username, block_keys)
        for student_module, usage_key in student_modules:
            if student_module.state is None:
                continue

            for field in json.loads(student_module.state):
                yield (usage_key, field, student_module.modified)

    def get_history(self, username, block_key, scope=Scope.user_state):
        """We don't guarantee that history for many blocks will be fast."""
        assert self.user.username == username
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        raise NotImplementedError()

    def iter_all_for_block(self, block_key, scope=Scope.user_state, batch_size=None):
        """
        You get no ordering guarantees. Fetching will happen in batch_size
        increments. If you're using this method, you should be running in an
        async task.
        """
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        raise NotImplementedError()

    def iter_all_for_course(self, course_key, block_type=None, scope=Scope.user_state, batch_size=None):
        """
        You get no ordering guarantees. Fetching will happen in batch_size
        increments. If you're using this method, you should be running in an
        async task.
        """
        if scope != Scope.user_state:
            raise ValueError("Only Scope.user_state is supported")
        raise NotImplementedError()
