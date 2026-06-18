"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _Flavors = _interopRequireDefault(require("../config/Flavors"));

/*
 * Copyright 2020 Bitnine Co., Ltd.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
var CypherService = /*#__PURE__*/function () {
  function CypherService(agensDatabaseHelper) {
    (0, _classCallCheck2["default"])(this, CypherService);
    this._agensDatabaseHelper = agensDatabaseHelper;
  }

  (0, _createClass2["default"])(CypherService, [{
    key: "executeCypher",
    value: function () {
      var _executeCypher = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(query) {
        var resultSet;
        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                if (query) {
                  _context.next = 4;
                  break;
                }

                throw new Error('Query not entered!');

              case 4:
                _context.prev = 4;
                _context.next = 7;
                return this._agensDatabaseHelper.execute(query);

              case 7:
                resultSet = _context.sent;
                return _context.abrupt("return", this.createResult(resultSet));

              case 11:
                _context.prev = 11;
                _context.t0 = _context["catch"](4);
                throw _context.t0;

              case 14:
              case "end":
                return _context.stop();
            }
          }
        }, _callee, this, [[4, 11]]);
      }));

      function executeCypher(_x) {
        return _executeCypher.apply(this, arguments);
      }

      return executeCypher;
    }()
  }, {
    key: "createResult",
    value: function createResult(resultSet) {
      var result = {
        rows: null,
        columns: null,
        rowCount: null,
        command: null
      };
      var targetItem = resultSet;

      if (Array.isArray(resultSet)) {
        targetItem = resultSet.pop();
      }

      var cypherRow = targetItem.rows;

      if (this._agensDatabaseHelper.flavor === _Flavors["default"].AGENS) {
        try {
          cypherRow = this._convertRowToResult(targetItem);
        } catch (e) {
          console.error("FixMe!");
        }
      }

      result = {
        rows: cypherRow,
        columns: this._getColumns(targetItem),
        rowCount: this._getRowCount(targetItem),
        command: this._getCommand(targetItem)
      };
      return result;
    }
  }, {
    key: "_getColumns",
    value: function _getColumns(resultSet) {
      return resultSet.fields.map(function (field) {
        return field.name;
      });
    }
  }, {
    key: "_getRowCount",
    value: function _getRowCount(resultSet) {
      return resultSet.rowCount;
    }
  }, {
    key: "_getCommand",
    value: function _getCommand(resultSet) {
      return resultSet.command;
    }
  }, {
    key: "_convertRowToResult",
    value: function _convertRowToResult(resultSet) {
      var _this = this;

      return resultSet.rows.map(function (row) {
        var convetedObject = {};

        for (var k in row) {
          var typeName = row[k].constructor.name;

          if (typeName === 'Path') {
            convetedObject[k] = _this.convertPath(row[k]);
          } else if (typeName === 'Vertex') {
            convetedObject[k] = _this.convertVertex(row[k]);
          } else if (typeName === 'Edge') {
            convetedObject[k] = _this.convertEdge(row[k]);
          } else {
            convetedObject[k] = row[k];
          }
        }

        return convetedObject;
      });
    }
  }, {
    key: "convertPath",
    value: function convertPath(_ref) {
      var vertices = _ref.vertices,
          edges = _ref.edges,
          start = _ref.start,
          end = _ref.end,
          len = _ref.len;
      var result = []; // vertex

      for (var idx in vertices) {
        result.push(this.convertVertex(vertices[idx]));
      } // edge


      for (var _idx in edges) {
        result.push(this.convertEdge(edges[_idx]));
      }

      return result;
    }
  }, {
    key: "convertEdge",
    value: function convertEdge(_ref2) {
      var label = _ref2.label,
          id = _ref2.id,
          start = _ref2.start,
          end = _ref2.end,
          props = _ref2.props;
      return {
        label: label,
        id: "".concat(id.oid, ".").concat(id.id),
        start: "".concat(start.oid, ".").concat(start.id),
        end: "".concat(end.oid, ".").concat(end.id),
        properties: props
      };
    }
  }, {
    key: "convertVertex",
    value: function convertVertex(_ref3) {
      var label = _ref3.label,
          id = _ref3.id,
          props = _ref3.props;
      return {
        label: label,
        id: "".concat(id.oid, ".").concat(id.id),
        properties: props
      };
    }
  }]);
  return CypherService;
}();

module.exports = CypherService;
//# sourceMappingURL=cypherService.js.map