import React, { useState } from 'react';
import './cozyGameplay.css';

export interface Recipe {
  id: string;
  name: string;
  icon: string;
  ingredients: string;
  effect: string;
  staminaRestore: number;
}

export const RECIPES: Recipe[] = [
  {
    id: 'grilled_trout',
    name: '香煎碧溪彩鲑',
    icon: '🥘',
    ingredients: '碧溪彩鲑 ×1, 晨露薄荷 ×1',
    effect: '移动速度 +20%，持续 5 分钟',
    staminaRestore: 45,
  },
  {
    id: 'berry_tart',
    name: '发光野莓松饼',
    icon: '🥧',
    ingredients: '发光野莓 ×2, 纯麦粉 ×1',
    effect: '采集稀有率 +15%',
    staminaRestore: 30,
  },
  {
    id: 'mint_tea',
    name: '晨露薄荷清茶',
    icon: '🍵',
    ingredients: '晨露薄荷 ×2, 山泉水 ×1',
    effect: '消除疲劳，心流专注状态',
    staminaRestore: 25,
  },
  {
    id: 'pumpkin_soup',
    name: '黄金南瓜暖心浓汤',
    icon: '🥣',
    ingredients: '仙境金南瓜 ×1, 鲜牛奶 ×1',
    effect: '好感度赠礼额外 +50%',
    staminaRestore: 60,
  },
];

export interface CozyCookingProps {
  onCookDish: (dish: Recipe) => void;
  onClose: () => void;
}

export const CozyCookingSystem: React.FC<CozyCookingProps> = ({ onCookDish, onClose }) => {
  const [cookingId, setCookingId] = useState<string | null>(null);
  const [cookedDish, setCookedDish] = useState<Recipe | null>(null);

  const handleStartCooking = (recipe: Recipe) => {
    setCookingId(recipe.id);
    setCookedDish(null);

    setTimeout(() => {
      setCookingId(null);
      setCookedDish(recipe);
      onCookDish(recipe);
    }, 1200);
  };

  return (
    <div className="cozy-cooking-modal" data-testid="cozy-cooking-modal">
      <div className="cozy-cooking-card">
        <div className="cozy-modal-header">
          <h3>🍳 林间野炊工坊</h3>
          <button type="button" className="cozy-close-btn" data-testid="cozy-cooking-close" onClick={onClose}>✕</button>
        </div>

        <div className="cozy-cooking-body">
          <div className="cozy-recipe-list">
            {RECIPES.map((recipe) => (
              <div key={recipe.id} className="cozy-recipe-item" data-testid={`recipe-${recipe.id}`}>
                <span className="recipe-icon">{recipe.icon}</span>
                <div className="recipe-details">
                  <h4>{recipe.name}</h4>
                  <p className="recipe-ingredients">所需材料：{recipe.ingredients}</p>
                  <p className="recipe-buff">✨ 滋补效果：{recipe.effect}</p>
                </div>
                <button
                  type="button"
                  className="cook-action-btn"
                  data-testid={`cook-btn-${recipe.id}`}
                  disabled={cookingId !== null}
                  onClick={() => handleStartCooking(recipe)}
                >
                  {cookingId === recipe.id ? '烹制中...' : '烹调 (+精力' + recipe.staminaRestore + ')'}
                </button>
              </div>
            ))}
          </div>

          {cookingId && (
            <div className="cooking-pot-animation">
              <span className="pot-icon animate-spin">🍲</span>
              <p>咕嘟咕嘟——香气在林间炊烟中弥漫...</p>
            </div>
          )}

          {cookedDish && (
            <div className="cooked-success-card">
              <span className="dish-icon animate-bounce">{cookedDish.icon}</span>
              <h4>{cookedDish.name} 出锅啦！</h4>
              <p>恢复了 {cookedDish.staminaRestore} 点精力值！</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
