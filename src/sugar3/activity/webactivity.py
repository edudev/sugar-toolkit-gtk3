# Copyright (C) 2013 Daniel Narvaez
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

import json
import os

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import WebKit2
from gi.repository import Gtk
from gi.repository import GdkX11
assert GdkX11

from gi.repository import SugarExt
from sugar3.activity import activity
from sugar3.presence import presenceservice

import dbus
import dbus.service

_ACTIVITY_SERVICE_NAME = 'org.laptop.Activity'
_ACTIVITY_SERVICE_PATH = '/org/laptop/Activity'
_ACTIVITY_INTERFACE = 'org.laptop.Activity'


class ActivityService(dbus.service.Object):

    def __init__(self, webactivity):
        # webactivity.realize()

        activity_id = webactivity.get_id()
        service_name = _ACTIVITY_SERVICE_NAME + activity_id
        object_path = _ACTIVITY_SERVICE_PATH + '/' + activity_id

        bus = dbus.SessionBus()
        bus_name = dbus.service.BusName(service_name, bus=bus)
        dbus.service.Object.__init__(self, bus_name, object_path)

        self._activity = webactivity

    @dbus.service.method(_ACTIVITY_INTERFACE)
    def InviteContact(self, account_path, contact_id):
        self._activity.invite(account_path, contact_id)

    # TODO: think of better names
    @dbus.service.method(_ACTIVITY_INTERFACE)
    def Share(self, togetherjs_id):
        self._activity.set_up_sharing(str(togetherjs_id))

    @dbus.service.method(_ACTIVITY_INTERFACE)
    def ShareClosed(self):
        self._activity.stop_sharing()

    # these are called by sugar so they should be implemented
    # yet these have little meaning to web activities (that's just a guess)
    @dbus.service.method(_ACTIVITY_INTERFACE)
    def SetActive(self, active):
        pass

    @dbus.service.method(_ACTIVITY_INTERFACE)
    def HandleViewSource(self):
        pass

    @dbus.service.method(_ACTIVITY_INTERFACE,
                         async_callbacks=('async_cb', 'async_err_cb'))
    def GetDocumentPath(self, async_cb, async_err_cb):
        pass


class WebActivity(Gtk.Window):
    def __init__(self, handle):
        Gtk.Window.__init__(self)

        self._activity_id = handle.activity_id
        self._object_id = handle.object_id
        self._bundle_id = os.environ["SUGAR_BUNDLE_ID"]
        self._bundle_path = os.environ["SUGAR_BUNDLE_PATH"]
        self._inspector_visible = False
        self._shared_activity = None
        self._bus = ActivityService(self)

        self.set_decorated(False)
        self.maximize()

        self.connect("key-press-event", self._key_press_event_cb)
        self.connect('realize', self._realize_cb)
        self.connect('destroy', self._destroy_cb)

        context = WebKit2.WebContext.get_default()
        context.register_uri_scheme("activity", self._app_scheme_cb, None)

        self._web_view = WebKit2.WebView()
        self._web_view.connect("load-changed", self._loading_changed_cb)

        self.add(self._web_view)
        self._web_view.show()

        settings = self._web_view.get_settings()
        settings.set_property("enable-developer-extras", True)

        uri = "activity://%s/index.html" % self._bundle_id
        if handle.uri:
            # uri += "#&togetherjs=" + handle.uri
            uri = handle.uri
        self._web_view.load_uri(uri)

        self.set_title(activity.get_bundle_name())

        # TODO: implement proper sharing
        # the only thing left to do should be when the user accepts an invite
        # self._shared_activity needs to be assigned from the handle
        # currently this is not needed, but it will be
        # Also, sugar's core might be interested in some signals
        # 'shared' and 'joined' signals should be enough

    def run_main_loop(self):
        Gtk.main()

    def _realize_cb(self, window):
        xid = window.get_window().get_xid()
        SugarExt.wm_set_bundle_id(xid, self._bundle_id)
        SugarExt.wm_set_activity_id(xid, str(self._activity_id))

    def _destroy_cb(self, window):
        dbus.service.Object.remove_from_connection(self._bus)
        self.destroy()
        Gtk.main_quit()

    def _loading_changed_cb(self, web_view, load_event):
        if load_event == WebKit2.LoadEvent.FINISHED:
            key = os.environ["SUGAR_APISOCKET_KEY"]
            port = os.environ["SUGAR_APISOCKET_PORT"]

            env_json = json.dumps({"apiSocketKey": key,
                                   "apiSocketPort": port,
                                   "activityId": self._activity_id,
                                   "bundleId": self._bundle_id,
                                   "objectId": self._object_id,
                                   "activityName": activity.get_bundle_name()})

            script = """
                     var environment = %s;

                     if (window.sugar === undefined) {
                         window.sugar = {};
                     }

                     window.sugar.environment = environment;

                     if (window.sugar.onEnvironmentSet)
                         window.sugar.onEnvironmentSet();
                    """ % env_json

            self._web_view.run_javascript(script, None, None, None)

    def _key_press_event_cb(self, window, event):
        key_name = Gdk.keyval_name(event.keyval)

        if event.get_state() & Gdk.ModifierType.CONTROL_MASK and \
           event.get_state() & Gdk.ModifierType.SHIFT_MASK:
            if key_name == "I":
                inspector = self._web_view.get_inspector()
                if self._inspector_visible:
                    inspector.close()
                    self._inspector_visible = False
                else:
                    inspector.show()
                    self._inspector_visible = True

                return True

    def _app_scheme_cb(self, request, user_data):
        path = os.path.join(self._bundle_path,
                            os.path.relpath(request.get_path(), "/"))

        request.finish(Gio.File.new_for_path(path).read(None),
                       -1, Gio.content_type_guess(path, None)[0])

    def get_id(self):
        return self._activity_id

    def get_bundle_id(self):
        return os.environ['SUGAR_BUNDLE_ID']

    # maybe move all this sharing in an other class and derive from it?
    # this might tidy up some code in activity.Activity
    # TODO: maybe some debug logging would be nice
    def set_up_sharing(self, togetherjs_id):
        if self._shared_activity:
            # not sure when or how, yet just to be safe
            return

        # TODO: set some basic metadata options from journal object
        self.metadata = {}

        # how to get togetherjs_id to the other side?
        # the properties that can be sent seem to be only these:
        # id, color, type, name, tags
        # I guess the winner is 'tags'
        # probably this needs to be concatenated, but how?
        extra_dict = {'tags': togetherjs_id}

        pservice = presenceservice.get_instance()
        mesh_instance = pservice.get_activity(self._activity_id,
                                              warn_if_none=False)
        if mesh_instance is None:
            pservice.connect('activity-shared', self.__shared_cb)
            pservice.share_activity(self, private=True, properties=extra_dict)
        else:
            # not sure if/when this will be executed
            self._shared_activity = mesh_instance
            self._join_id = self._shared_activity.connect('joined',
                                                          self.__joined_cb)
            if not self._shared_activity.props.joined:
                self._shared_activity.join()
            else:
                self.__joined_cb(self._shared_activity, True, None)

    def stop_sharing(self):
        if not self._shared_activity:
            return

        # not sure if this will be enough for a proper leave
        self._shared_activity.leave()
        self._shared_activity = None

    def __joined_cb(self, shared_activity, success, err):
        self._shared_activity.disconnect(self._join_id)
        self._join_id = None
        if not success:
            return

        # don't really care, not needed

    def __shared_cb(self, ps, success, shared_activity, err):
        if not success:
            return

        self._shared_activity = shared_activity

    def invite(self, account_path, contact_id):
        if not self._shared_activity:
            # perhaps do it as activity.Activity
            # queue the invites, start sharing, send the invites
            return
        pservice = presenceservice.get_instance()
        buddy = pservice.get_buddy(account_path, contact_id)
        if buddy:
            self._shared_activity.invite(
                buddy, '', self._invite_response_cb)

    def _invite_response_cb(self, error):
        pass
