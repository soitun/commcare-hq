/* globals hqDefine, ko, $ */

hqDefine('case/js/case_property_table', function(){
    'use strict';

    var CasePropertyTable = function(){
        var self = this;

        self.propertyName = ko.observable();
        console.log(self.propertyName());
        self.changes = ko.observable();
    };
    return CasePropertyTable;
});
