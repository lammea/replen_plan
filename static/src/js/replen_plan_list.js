odoo.define('replen_plan.ReplenPlanListController', function (require) {
    "use strict";

    var ListController = require('web.ListController');
    var ListView = require('web.ListView');
    var viewRegistry = require('web.view_registry');
    var core = require('web.core');
    var session = require('web.session');

    var ReplenPlanListController = ListController.extend({
        _onOpenRecord: function (event) {
            var self = this;
            var record = this.model.get(event.data.id);
            
            this._rpc({
                model: 'replen.plan',
                method: 'open_form',
                args: [[record.res_id]],
            }).then(function (action) {
                // Ajouter l'ID du plan et l'état dans l'URL
                var state = action.context.state_view || 'draft';
                var url = window.location.pathname + '?plan_id=' + record.res_id + '&state=' + state;
                window.history.pushState({}, '', url);
                
                self.do_action(action);
            });
        },
    });

    var ReplenPlanFormController = require('web.FormController').extend({
        init: function () {
            this._super.apply(this, arguments);
            this._onHashChange = this._onHashChange.bind(this);
            window.addEventListener('popstate', this._onHashChange);
        },

        destroy: function () {
            window.removeEventListener('popstate', this._onHashChange);
            this._super.apply(this, arguments);
        },

        _onHashChange: function (event) {
            var params = new URLSearchParams(window.location.search);
            var planId = params.get('plan_id');
            var state = params.get('state');

            if (planId && state) {
                this._rpc({
                    model: 'replen.plan',
                    method: 'open_form',
                    args: [[parseInt(planId)]],
                    context: {'state_view': state}
                }).then(function (action) {
                    self.do_action(action);
                });
            }
        },
    });

    var ReplenPlanListView = ListView.extend({
        config: _.extend({}, ListView.prototype.config, {
            Controller: ReplenPlanListController,
        }),
    });

    var ReplenPlanFormView = require('web.FormView').extend({
        config: _.extend({}, require('web.FormView').prototype.config, {
            Controller: ReplenPlanFormController,
        }),
    });

    viewRegistry.add('replen_plan_list', ReplenPlanListView);
    viewRegistry.add('replen_plan_form', ReplenPlanFormView);

    return {
        ReplenPlanListView: ReplenPlanListView,
        ReplenPlanFormView: ReplenPlanFormView,
    };
}); 