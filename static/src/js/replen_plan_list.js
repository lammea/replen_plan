odoo.define('replen_plan.ReplenPlanListController', function (require) {
    "use strict";

    var ListController = require('web.ListController');
    var ListView = require('web.ListView');
    var viewRegistry = require('web.view_registry');

    var ReplenPlanListController = ListController.extend({
        _onOpenRecord: function (event) {
            var self = this;
            var record = this.model.get(event.data.id);
            
            this._rpc({
                model: 'replen.plan',
                method: 'open_form',
                args: [[record.res_id]],
            }).then(function (action) {
                self.do_action(action);
            });
        },
    });

    var ReplenPlanListView = ListView.extend({
        config: _.extend({}, ListView.prototype.config, {
            Controller: ReplenPlanListController,
        }),
    });

    viewRegistry.add('replen_plan_list', ReplenPlanListView);

    return ReplenPlanListView;
}); 